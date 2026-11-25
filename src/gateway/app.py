"""The FastAPI application, built by a factory so tests can inject their own parts."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway import __version__, services
from gateway.admin import router as admin_router
from gateway.config import Settings, get_settings
from gateway.db import create_tables, make_engine, make_session_factory
from gateway.deps import LimitedTeamDep, ProviderDep, SessionDep
from gateway.errors import GatewayError, InvalidRequestError, ProviderTimeoutError
from gateway.providers.base import Provider
from gateway.providers.factory import build_provider
from gateway.ratelimit import RateLimiter
from gateway.schemas import ChatRequest, ChatResponse


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

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.provider = provider if provider is not None else build_provider(settings)
    app.state.rate_limiter = RateLimiter()
    app.include_router(admin_router)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

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

    @app.post("/v1/chat/completions", response_model=ChatResponse)
    async def chat_completions(
        body: ChatRequest,
        request: Request,
        http_response: Response,
        team: LimitedTeamDep,
        session: SessionDep,
        provider: ProviderDep,
    ) -> ChatResponse:
        if body.stream:
            raise InvalidRequestError("stream=true is not supported yet")
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
            team_id=team.id,
            request_id=request.state.request_id,
            provider=served_by,
            response=response,
        )
        http_response.headers["x-gateway-provider"] = served_by
        return response

    return app
