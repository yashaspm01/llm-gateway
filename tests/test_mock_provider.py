import time

import pytest

from gateway.errors import ProviderError
from gateway.providers.mock import MockProvider
from gateway.schemas import ChatMessage, ChatRequest


def request() -> ChatRequest:
    return ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi there")])


async def test_returns_reply_and_usage():
    response = await MockProvider().complete(request())
    assert "You said: hi there" in response.choices[0].message.content
    assert response.usage.prompt_tokens == 2


async def test_raises_configured_failure():
    provider = MockProvider(failure=ProviderError("down"))
    with pytest.raises(ProviderError):
        await provider.complete(request())


async def test_counts_calls():
    provider = MockProvider()
    await provider.complete(request())
    await provider.complete(request())
    assert provider.calls == 2


async def test_latency_is_awaited():
    start = time.perf_counter()
    await MockProvider(latency_s=0.05).complete(request())
    assert time.perf_counter() - start >= 0.05
