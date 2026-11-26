"""The FastAPI application, built by a factory so tests can inject their own parts."""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway import __version__, services
from gateway.admin import router as admin_router
from gateway.config import Settings, get_settings
from gateway.db import create_tables, make_engine, make_session_factory
from gateway.deps import LimitedTeamDep, ProviderDep, SessionDep, require_admin
from gateway.errors import GatewayError, ProviderError, ProviderTimeoutError
from gateway.logging_setup import request_id_var
from gateway.metrics import Metrics
from gateway.providers.base import Provider
from gateway.providers.factory import build_provider
from gateway.ratelimit import RateLimiter
from gateway.schemas import ChatChunk, ChatRequest, Usage
from gateway.status import provider_status
from gateway.streams import close_stream

log = logging.getLogger(__name__)
access_log = logging.getLogger("gateway.access")

# Client-supplied request IDs end up in logs and headers, so only accept boring ones.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _sse(chunk: ChatChunk) -> str:
    return f"data: {chunk.model_dump_json()}\n\n"


def _sse_error(exc: GatewayError) -> str:
    body = {"error": {"message": exc.message, "type": exc.error_type}}
    return f"data: {json.dumps(body)}\n\n"


async def _prepend(first: ChatChunk, rest: AsyncIterator[ChatChunk]) -> AsyncIterator[ChatChunk]:
    yield first
    async for chunk in rest:
        yield chunk


async def _first_chunk(
    iterator: AsyncIterator[ChatChunk], provider_name: str, timeout_s: float
) -> ChatChunk:
    """Wait for the first chunk. Errors here can still become proper HTTP status codes."""
    try:
        async with asyncio.timeout(timeout_s):
            return await anext(iterator)
    except StopAsyncIteration as exc:
        await close_stream(iterator)
        raise ProviderError(f"Provider '{provider_name}' returned an empty stream") from exc
    except TimeoutError as exc:
        await close_stream(iterator)
        raise ProviderTimeoutError(
            f"Provider '{provider_name}' sent nothing within {timeout_s}s"
        ) from exc
    except BaseException:
        await close_stream(iterator)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if app.state.settings.auto_create_tables:
        await create_tables(app.state.engine)  # tests only; real databases use migrations
    yield
    aclose = getattr(app.state.provider, "aclose", None)
    if aclose is not None:
        await aclose()
    await app.state.engine.dispose()


def create_app(provider: Provider | None = None, settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    timeout_s = settings.provider_timeout_s
    engine = make_engine(settings.database_url)
    metrics = Metrics()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.provider = provider if provider is not None else build_provider(settings)
    app.state.rate_limiter = RateLimiter()
    app.state.metrics = metrics
    app.include_router(admin_router)

    @app.middleware("http")
    async def observability_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _SAFE_REQUEST_ID.match(supplied) else uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            # The route TEMPLATE, never the raw path, keeps metric labels bounded.
            route_path = getattr(request.scope.get("route"), "path", "unmatched")
            metrics.observe_http(request.method, route_path, status, elapsed)
            access_log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "route": route_path,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                    "team_id": getattr(request.state, "team_id", None),
                },
            )
            request_id_var.reset(token)

    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        body = {"error": {"message": exc.message, "type": exc.error_type}}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        body = {
            "error": {
                "message": "Invalid request",
                "type": "invalid_request_error",
                "details": details,
            }
        }
        return JSONResponse(status_code=400, content=body)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/metrics", dependencies=[Depends(require_admin)], include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        metrics.update_circuits(provider_status(request.app.state.provider))
        return Response(metrics.render(), media_type=Metrics.content_type)

    async def complete_chat(
        body: ChatRequest,
        request_id: str,
        team_id: int,
        session: AsyncSession,
        provider: Provider,
    ) -> Response:
        try:
            async with asyncio.timeout(timeout_s):
                response = await provider.complete(body)
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Provider '{provider.name}' did not answer within {timeout_s}s"
            ) from exc
        served_by = response.provider or provider.name
        await services.record_usage(
            session,
            team_id=team_id,
            request_id=request_id,
            provider=served_by,
            response=response,
        )
        metrics.observe_chat(
            provider=served_by,
            stream=False,
            team_id=team_id,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        return JSONResponse(
            response.model_dump(mode="json"), headers={"x-gateway-provider": served_by}
        )

    async def stream_chat(
        body: ChatRequest, request_id: str, team_id: int, provider: Provider
    ) -> Response:
        iterator = provider.stream(body)
        # Wait for the first chunk BEFORE answering, so a failing provider
        # is still a real 502/503/504.
        first = await _first_chunk(iterator, provider.name, timeout_s)
        served_by = first.provider or provider.name
        usage: Usage | None = None
        pieces: list[str] = []

        async def record() -> None:
            if usage is not None:
                prompt_tokens, completion_tokens = usage.prompt_tokens, usage.completion_tokens
            else:  # the provider sent no usage: estimate from the words we relayed
                prompt_tokens = sum(len(m.content.split()) for m in body.messages)
                completion_tokens = len("".join(pieces).split())
            try:
                # A fresh session: the request-scoped one may already be closed by now.
                async with app.state.session_factory() as record_session:
                    await services.record_tokens(
                        record_session,
                        team_id=team_id,
                        request_id=request_id,
                        provider=served_by,
                        model=first.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
            except Exception:
                log.exception("could not record usage for a streamed response")
            metrics.observe_chat(
                provider=served_by,
                stream=True,
                team_id=team_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        async def events() -> AsyncIterator[str]:
            nonlocal usage
            completed = False
            try:
                async for chunk in _prepend(first, iterator):
                    if chunk.usage is not None:
                        usage = chunk.usage
                    pieces.extend(c.delta.content for c in chunk.choices if c.delta.content)
                    yield _sse(chunk)
                completed = True
            except ProviderError as exc:  # too late for a status code: send an error event
                log.warning("stream from %s failed mid-way: %s", served_by, exc.message)
                yield _sse_error(exc)
            finally:
                await close_stream(iterator)  # frees the upstream connection (and stops billing)
                await record()  # best effort if the client disconnected
            if completed:
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "x-gateway-provider": served_by,
                "cache-control": "no-cache",
                "x-accel-buffering": "no",  # stop proxies from buffering the stream
            },
        )

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        body: ChatRequest,
        request: Request,
        team: LimitedTeamDep,
        session: SessionDep,
        provider: ProviderDep,
    ) -> Response:
        request_id: str = request.state.request_id
        if body.stream:
            return await stream_chat(body, request_id, team.id, provider)
        return await complete_chat(body, request_id, team.id, session, provider)

    return app
