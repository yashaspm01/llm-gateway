"""Circuit breaker: stop calling a provider that is clearly failing, and probe it to recover."""

import logging
import time
from collections.abc import AsyncIterator, Callable
from enum import StrEnum

from gateway.errors import ProviderError, ProviderUnavailableError
from gateway.providers.base import Provider
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse
from gateway.streams import close_stream

log = logging.getLogger(__name__)


class State(StrEnum):
    CLOSED = "closed"  # normal: calls flow
    OPEN = "open"  # tripped: calls are rejected immediately
    HALF_OPEN = "half_open"  # testing: one probe call decides


class CircuitBreaker:
    def __init__(
        self,
        name: str = "circuit",
        *,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout_s < 0:
            raise ValueError("recovery_timeout_s cannot be negative")
        self.name = name
        self._threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._clock = clock  # injectable, like the rate limiter's
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> State:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    def retry_in(self) -> float:
        """Seconds until a probe will be allowed (0 unless the circuit is open)."""
        if self._state is not State.OPEN:
            return 0.0
        return max(0.0, self._recovery_timeout_s - (self._clock() - self._opened_at))

    def allow(self) -> bool:
        """May a call go through right now? In half-open state, only one probe at a time."""
        if self._state is State.OPEN:
            if self._clock() - self._opened_at < self._recovery_timeout_s:
                return False
            self._transition(State.HALF_OPEN)
        if self._state is State.HALF_OPEN:
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        if self._state is State.HALF_OPEN:
            self._transition(State.CLOSED)
        if self._state is State.CLOSED:
            self._failures = 0
        self._probe_in_flight = False

    def record_failure(self) -> None:
        if self._state is State.HALF_OPEN:
            self._open()
        elif self._state is State.CLOSED:
            self._failures += 1
            if self._failures >= self._threshold:
                self._open()
        self._probe_in_flight = False

    def record_ignored(self) -> None:
        """An outcome that says nothing about the provider's health. Frees a probe slot."""
        self._probe_in_flight = False

    def _open(self) -> None:
        self._opened_at = self._clock()
        self._transition(State.OPEN)

    def _transition(self, new: State) -> None:
        if new is self._state:
            return
        log.warning("circuit %s: %s -> %s", self.name, self._state, new)
        self._state = new
        if new is State.CLOSED:
            self._failures = 0
        if new is State.HALF_OPEN:
            self._probe_in_flight = False


class CircuitBreakerProvider:
    """Wraps a Provider with a circuit breaker."""

    def __init__(self, inner: Provider, breaker: CircuitBreaker) -> None:
        self.inner = inner
        self.breaker = breaker
        self.name = inner.name

    async def aclose(self) -> None:
        aclose = getattr(self.inner, "aclose", None)
        if aclose is not None:
            await aclose()

    def status(self) -> list[dict[str, object]]:
        return [
            {
                "name": self.name,
                "state": self.breaker.state.value,
                "consecutive_failures": self.breaker.failures,
                "retry_in_s": round(self.breaker.retry_in(), 1),
            }
        ]

    def _unavailable(self) -> ProviderUnavailableError:
        return ProviderUnavailableError(
            f"Provider '{self.name}' is temporarily disabled (circuit open)",
            retry_after_s=self.breaker.retry_in(),
        )

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not self.breaker.allow():
            raise self._unavailable()
        outcome_recorded = False
        try:
            response = await self.inner.complete(request)
            self.breaker.record_success()
            outcome_recorded = True
            return response
        except ProviderError as exc:
            if exc.retryable:
                self.breaker.record_failure()
            else:
                self.breaker.record_ignored()  # e.g. a bad request is not the provider's fault
            outcome_recorded = True
            raise
        finally:
            if not outcome_recorded:  # cancelled or an unexpected bug: never strand a probe
                self.breaker.record_ignored()

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        if not self.breaker.allow():
            raise self._unavailable()
        outcome_recorded = False
        inner = self.inner.stream(request)
        try:
            async for chunk in inner:
                if not outcome_recorded:  # the first chunk proves the provider answered
                    self.breaker.record_success()
                    outcome_recorded = True
                yield chunk
            if not outcome_recorded:  # an empty stream still means the provider answered
                self.breaker.record_success()
                outcome_recorded = True
        except ProviderError as exc:
            if exc.retryable:
                self.breaker.record_failure()  # also counts if it breaks mid-stream
            else:
                self.breaker.record_ignored()
            outcome_recorded = True
            raise
        finally:
            if not outcome_recorded:  # cancelled before the first chunk: free the probe slot
                self.breaker.record_ignored()
            await close_stream(inner)
