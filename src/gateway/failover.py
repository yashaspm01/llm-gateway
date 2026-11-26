"""Try providers in priority order until one answers."""

import logging
from collections.abc import AsyncIterator, Sequence

from gateway.errors import AllProvidersFailedError, ProviderError
from gateway.providers.base import Provider
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse
from gateway.status import provider_status
from gateway.streams import close_stream

log = logging.getLogger(__name__)


def _all_failed(failures: list[tuple[str, ProviderError]]) -> AllProvidersFailedError:
    hints = [exc.retry_after_s for _, exc in failures if exc.retry_after_s is not None]
    summary = "; ".join(f"{name}: {exc.message}" for name, exc in failures)
    return AllProvidersFailedError(
        f"All providers failed. {summary}", retry_after_s=min(hints) if hints else None
    )


class FailoverProvider:
    def __init__(self, providers: Sequence[Provider]) -> None:
        if not providers:
            raise ValueError("FailoverProvider needs at least one provider")
        self.providers = list(providers)
        self.name = "failover"

    async def aclose(self) -> None:
        for provider in self.providers:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    def status(self) -> list[dict[str, object]]:
        return [row for provider in self.providers for row in provider_status(provider)]

    async def complete(self, request: ChatRequest) -> ChatResponse:
        failures: list[tuple[str, ProviderError]] = []
        for provider in self.providers:
            try:
                return await provider.complete(request)
            except ProviderError as exc:
                log.warning("provider=%s failed (%s); trying the next", provider.name, exc.message)
                failures.append((provider.name, exc))
        raise _all_failed(failures)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Fail over only before the first chunk. After that the stream belongs to one provider."""
        failures: list[tuple[str, ProviderError]] = []
        for provider in self.providers:
            inner = provider.stream(request)
            try:
                try:
                    first = await anext(inner)
                except StopAsyncIteration:
                    return
                except ProviderError as exc:
                    log.warning("provider=%s failed (%s); trying next", provider.name, exc.message)
                    failures.append((provider.name, exc))
                    continue
                yield first
                async for chunk in inner:
                    yield chunk
                return
            finally:
                await close_stream(inner)
        raise _all_failed(failures)
