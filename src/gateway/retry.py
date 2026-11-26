"""Retry with exponential backoff and full jitter."""

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from gateway.errors import ProviderError
from gateway.providers.base import Provider
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse
from gateway.status import provider_status
from gateway.streams import close_stream

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.2
    max_delay_s: float = 5.0

    def delay_for(
        self, attempt: int, retry_after_s: float | None, rng: random.Random
    ) -> float | None:
        """Seconds to wait before the next attempt, or None if we won't wait that long."""
        ceiling = min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1))
        backoff = rng.uniform(0.0, ceiling)  # "full jitter"
        if retry_after_s is None:
            return backoff
        if retry_after_s > self.max_delay_s:
            return None
        return max(backoff, retry_after_s)


class RetryingProvider:
    """Wraps any Provider and retries its transient failures.

    For streams, retries only happen before the first chunk: once bytes have gone
    to the client, a stream cannot be restarted or handed to another provider.
    """

    def __init__(
        self,
        inner: Provider,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.inner = inner
        self.policy = policy
        self.name = inner.name
        self._sleep = sleep  # injectable, so tests never really wait
        self._rng = rng or random.Random()

    async def aclose(self) -> None:
        aclose = getattr(self.inner, "aclose", None)
        if aclose is not None:
            await aclose()

    def status(self) -> list[dict[str, object]]:
        return provider_status(self.inner)

    def _next_delay(self, exc: ProviderError, attempt: int) -> float | None:
        """How long to wait before retrying, or None if we should give up and raise."""
        if not exc.retryable or attempt >= self.policy.max_attempts:
            return None
        delay = self.policy.delay_for(attempt, exc.retry_after_s, self._rng)
        if delay is not None:
            log.warning(
                "provider=%s attempt=%d/%d failed: %s; retrying in %.2fs",
                self.name,
                attempt,
                self.policy.max_attempts,
                exc.message,
                delay,
            )
        return delay

    async def complete(self, request: ChatRequest) -> ChatResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self.inner.complete(request)
            except ProviderError as exc:
                delay = self._next_delay(exc, attempt)
                if delay is None:
                    raise
                await self._sleep(delay)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        attempt = 0
        while True:
            attempt += 1
            inner = self.inner.stream(request)
            try:
                try:
                    first = await anext(inner)
                except StopAsyncIteration:
                    return
                except ProviderError as exc:
                    delay = self._next_delay(exc, attempt)
                    if delay is None:
                        raise
                    await self._sleep(delay)
                    continue
                yield first
                async for chunk in inner:  # after the first chunk, errors are not retried
                    yield chunk
                return
            finally:
                await close_stream(inner)
