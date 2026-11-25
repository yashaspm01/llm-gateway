"""Try providers in priority order until one answers."""

import logging
from collections.abc import Sequence

from gateway.errors import AllProvidersFailedError, ProviderError
from gateway.providers.base import Provider
from gateway.schemas import ChatRequest, ChatResponse
from gateway.status import provider_status

log = logging.getLogger(__name__)


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
                log.warning(
                    "provider=%s failed (%s); trying the next one", provider.name, exc.message
                )
                failures.append((provider.name, exc))
        hints = [exc.retry_after_s for _, exc in failures if exc.retry_after_s is not None]
        summary = "; ".join(f"{name}: {exc.message}" for name, exc in failures)
        raise AllProvidersFailedError(
            f"All providers failed. {summary}", retry_after_s=min(hints) if hints else None
        )
