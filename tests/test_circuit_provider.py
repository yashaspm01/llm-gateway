import asyncio

import pytest
from helpers import Scripted, make_response

from gateway.circuit import CircuitBreaker, CircuitBreakerProvider, State
from gateway.errors import ProviderError, ProviderUnavailableError
from gateway.schemas import ChatMessage, ChatRequest

REQUEST = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(outcomes, threshold=2, recovery=30.0):
    clock = FakeClock()
    inner = Scripted(outcomes, "p")
    breaker = CircuitBreaker(
        "p", failure_threshold=threshold, recovery_timeout_s=recovery, clock=clock
    )
    return CircuitBreakerProvider(inner, breaker), inner, breaker, clock


async def test_failures_open_the_circuit_and_calls_stop_reaching_the_provider():
    provider, inner, breaker, _ = build([ProviderError("x"), ProviderError("x"), make_response()])
    for _ in range(2):
        with pytest.raises(ProviderError):
            await provider.complete(REQUEST)
    assert breaker.state is State.OPEN
    with pytest.raises(ProviderUnavailableError):
        await provider.complete(REQUEST)
    assert inner.calls == 2  # the third call never reached the provider


async def test_open_circuit_error_is_not_retryable_and_says_when_to_come_back():
    provider, _, _, _ = build([ProviderError("x")], threshold=1, recovery=30)
    with pytest.raises(ProviderError):
        await provider.complete(REQUEST)
    with pytest.raises(ProviderUnavailableError) as info:
        await provider.complete(REQUEST)
    assert info.value.retryable is False
    assert info.value.retry_after_s == 30.0
    assert info.value.status_code == 503
    assert info.value.headers == {"Retry-After": "30"}


async def test_non_retryable_errors_do_not_open_the_circuit():
    provider, inner, breaker, _ = build([ProviderError("bad request", retryable=False)] * 3)
    for _ in range(3):
        with pytest.raises(ProviderError):
            await provider.complete(REQUEST)
    assert inner.calls == 3
    assert breaker.state is State.CLOSED


async def test_recovers_after_a_successful_probe():
    provider, inner, breaker, clock = build(
        [ProviderError("x"), ProviderError("x"), make_response(), make_response()]
    )
    for _ in range(2):
        with pytest.raises(ProviderError):
            await provider.complete(REQUEST)
    clock.now += 30
    reply = await provider.complete(REQUEST)  # the half-open probe
    assert reply.provider == "p"
    assert breaker.state is State.CLOSED
    await provider.complete(REQUEST)
    assert inner.calls == 4


class Hang:
    name = "hang"

    async def complete(self, request):
        await asyncio.sleep(10)


async def test_a_cancelled_probe_does_not_strand_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_timeout_s=10, clock=clock)
    breaker.record_failure()
    clock.now += 10
    provider = CircuitBreakerProvider(Hang(), breaker)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await provider.complete(REQUEST)
    assert breaker.state is State.HALF_OPEN
    assert breaker.allow()  # the probe slot was released


def test_status_reports_the_state():
    provider, _, _, _ = build([])
    assert provider.status() == [
        {"name": "p", "state": "closed", "consecutive_failures": 0, "retry_in_s": 0.0}
    ]
