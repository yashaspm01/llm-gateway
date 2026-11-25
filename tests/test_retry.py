import asyncio
import random

import pytest
from helpers import Scripted, make_response

from gateway.errors import ProviderError, ProviderTimeoutError
from gateway.retry import RetryingProvider, RetryPolicy
from gateway.schemas import ChatMessage, ChatRequest

REQUEST = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])
FAST = RetryPolicy(max_attempts=3, base_delay_s=0.1, max_delay_s=1.0)


class Sleeps:
    """A fake sleep: records how long we were asked to wait, without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def build(outcomes, policy=FAST):
    inner = Scripted(outcomes)
    sleeps = Sleeps()
    return RetryingProvider(inner, policy, sleep=sleeps, rng=random.Random(0)), inner, sleeps


async def test_success_on_first_try():
    provider, inner, sleeps = build([make_response()])
    await provider.complete(REQUEST)
    assert inner.calls == 1
    assert sleeps.delays == []


async def test_retries_then_succeeds():
    provider, inner, sleeps = build([ProviderError("blip"), ProviderError("blip"), make_response()])
    reply = await provider.complete(REQUEST)
    assert reply.choices[0].message.content == "hi"
    assert inner.calls == 3
    assert len(sleeps.delays) == 2
    assert sleeps.delays[0] <= 0.1  # first retry: ceiling is the base delay
    assert sleeps.delays[1] <= 0.2  # second retry: ceiling doubles


async def test_gives_up_after_max_attempts():
    provider, inner, sleeps = build([ProviderError("down")] * 3)
    with pytest.raises(ProviderError):
        await provider.complete(REQUEST)
    assert inner.calls == 3
    assert len(sleeps.delays) == 2  # no sleep after the final attempt


async def test_non_retryable_error_is_raised_immediately():
    provider, inner, sleeps = build([ProviderError("bad key", retryable=False), make_response()])
    with pytest.raises(ProviderError):
        await provider.complete(REQUEST)
    assert inner.calls == 1
    assert sleeps.delays == []


async def test_timeouts_are_retried():
    provider, inner, _ = build([ProviderTimeoutError("slow"), make_response()])
    await provider.complete(REQUEST)
    assert inner.calls == 2


async def test_unexpected_exceptions_are_not_retried():
    provider, inner, _ = build([ValueError("a bug in our own code")])
    with pytest.raises(ValueError):
        await provider.complete(REQUEST)
    assert inner.calls == 1


def test_backoff_delay_is_bounded_and_grows():
    policy = RetryPolicy(max_attempts=10, base_delay_s=1.0, max_delay_s=4.0)
    rng = random.Random(1)
    for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 4.0), (8, 4.0)]:
        delays = [policy.delay_for(attempt, None, rng) for _ in range(500)]
        assert all(0.0 <= d <= ceiling for d in delays)
        assert max(delays) > ceiling * 0.8  # jitter really spreads across the whole range


def test_retry_after_is_honored():
    policy = RetryPolicy(base_delay_s=0.1, max_delay_s=5.0)
    assert policy.delay_for(1, 2.0, random.Random(0)) == 2.0


async def test_retry_after_beyond_the_cap_gives_up():
    provider, inner, sleeps = build(
        [ProviderError("slow down", retry_after_s=60.0), make_response()]
    )
    with pytest.raises(ProviderError):
        await provider.complete(REQUEST)
    assert inner.calls == 1
    assert sleeps.delays == []


async def test_overall_deadline_cuts_retries_short():
    inner = Scripted([ProviderError("x", retry_after_s=3.0)] * 5)
    provider = RetryingProvider(inner, RetryPolicy(max_attempts=5, max_delay_s=5.0))  # real sleep
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await provider.complete(REQUEST)
    assert inner.calls == 1
