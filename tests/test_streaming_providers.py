import json

import httpx2
import pytest
from helpers import Scripted, collect, make_chunks

from gateway.circuit import CircuitBreaker, CircuitBreakerProvider, State
from gateway.errors import AllProvidersFailedError, ProviderError, ProviderUnavailableError
from gateway.failover import FailoverProvider
from gateway.providers.http import OpenAICompatibleProvider
from gateway.providers.mock import MockProvider
from gateway.retry import RetryingProvider, RetryPolicy
from gateway.schemas import ChatMessage, ChatRequest

REQUEST = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hello world")])
INSTANT = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)
BASE = "https://upstream.example/v1/"


def text_of(chunks):
    return "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)


# ---- the mock provider ----


async def test_mock_stream_yields_role_words_and_final_usage():
    chunks = await collect(MockProvider().stream(REQUEST))
    assert text_of(chunks) == "[mock] You said: hello world"
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert chunks[-1].usage.prompt_tokens == 2
    assert chunks[-1].usage.completion_tokens == 5
    assert all(c.provider == "mock" for c in chunks)


async def test_mock_stream_failure_before_the_first_chunk():
    with pytest.raises(ProviderError):
        await collect(MockProvider(failure=ProviderError("down")).stream(REQUEST))


async def test_mock_stream_failure_mid_stream():
    provider = MockProvider(failure=ProviderError("broke"), fail_after_chunks=2)
    seen = []
    with pytest.raises(ProviderError):
        async for chunk in provider.stream(REQUEST):
            seen.append(chunk)
    assert len(seen) == 3  # the role chunk plus two words


# ---- retries ----


async def test_stream_retries_before_the_first_chunk():
    inner = Scripted([ProviderError("blip"), make_chunks("a", "b")], "p")
    chunks = await collect(RetryingProvider(inner, INSTANT).stream(REQUEST))
    assert len(chunks) == 4
    assert inner.calls == 2


async def test_stream_does_not_retry_after_the_first_chunk():
    inner = Scripted([make_chunks("a", "b")[:2] + [ProviderError("boom")]], "p")
    seen = []
    with pytest.raises(ProviderError):
        async for chunk in RetryingProvider(inner, INSTANT).stream(REQUEST):
            seen.append(chunk)
    assert len(seen) == 2
    assert inner.calls == 1


async def test_stream_gives_up_after_max_attempts():
    inner = Scripted([ProviderError("down")] * 3, "p")
    with pytest.raises(ProviderError):
        await collect(RetryingProvider(inner, INSTANT).stream(REQUEST))
    assert inner.calls == 3


class Tracked:
    name = "tracked"

    def __init__(self):
        self.closed = False

    async def stream(self, request):
        try:
            for chunk in make_chunks("a", "b", "c"):
                yield chunk
        finally:
            self.closed = True


async def test_closing_a_retry_stream_closes_the_inner_stream():
    inner = Tracked()
    stream = RetryingProvider(inner, INSTANT).stream(REQUEST)
    await anext(stream)
    await stream.aclose()
    assert inner.closed


# ---- circuit breaker ----


async def test_circuit_stream_opens_after_repeated_failures():
    inner = Scripted([ProviderError("x")] * 5, "p")
    breaker = CircuitBreaker("p", failure_threshold=2)
    provider = CircuitBreakerProvider(inner, breaker)
    for _ in range(2):
        with pytest.raises(ProviderError):
            await collect(provider.stream(REQUEST))
    assert breaker.state is State.OPEN
    with pytest.raises(ProviderUnavailableError):
        await collect(provider.stream(REQUEST))
    assert inner.calls == 2


async def test_circuit_stream_counts_mid_stream_failures():
    inner = Scripted([make_chunks("a")[:2] + [ProviderError("boom")]], "p")
    breaker = CircuitBreaker("p", failure_threshold=1)
    with pytest.raises(ProviderError):
        await collect(CircuitBreakerProvider(inner, breaker).stream(REQUEST))
    assert breaker.state is State.OPEN


# ---- failover ----


async def test_stream_fails_over_before_the_first_chunk():
    primary = Scripted([ProviderError("down")], "primary")
    backup = Scripted([make_chunks("hi")], "backup")
    chunks = await collect(FailoverProvider([primary, backup]).stream(REQUEST))
    assert {c.provider for c in chunks} == {"backup"}


async def test_stream_does_not_fail_over_mid_stream():
    primary = Scripted([make_chunks("a")[:2] + [ProviderError("boom")]], "primary")
    backup = Scripted([make_chunks("hi")], "backup")
    with pytest.raises(ProviderError):
        await collect(FailoverProvider([primary, backup]).stream(REQUEST))
    assert backup.calls == 0


async def test_stream_all_providers_failing():
    a = Scripted([ProviderError("x")], "a")
    b = Scripted([ProviderError("y")], "b")
    with pytest.raises(AllProvidersFailedError):
        await collect(FailoverProvider([a, b]).stream(REQUEST))


# ---- the HTTP provider ----


def make_http(handler):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), base_url=BASE)
    return OpenAICompatibleProvider(name="up", base_url=BASE, api_key="k", client=client)


def event(content=None, finish=None, usage=None) -> str:
    body = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content is not None else {},
                "finish_reason": finish,
            }
        ],
    }
    if usage:
        body["usage"] = usage
        body["choices"] = []
    return json.dumps(body)


def sse_body(*events: str, done: bool = True) -> bytes:
    text = ": keep-alive\n\n" + "".join(f"data: {e}\n\n" for e in events)
    return (text + ("data: [DONE]\n\n" if done else "")).encode()


async def test_http_stream_parses_sse_and_asks_for_usage():
    seen = {}
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    def handler(request):
        seen["body"] = json.loads(request.content)
        payload = sse_body(event("Hel"), event("lo"), event(finish="stop"), event(usage=usage))
        return httpx2.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    chunks = await collect(make_http(handler).stream(REQUEST))
    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert text_of(chunks) == "Hello"
    assert chunks[-1].usage.total_tokens == 5
    assert all(c.provider == "up" for c in chunks)


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (400, False)])
async def test_http_stream_maps_status_errors(status, retryable):
    provider = make_http(lambda r: httpx2.Response(status))
    with pytest.raises(ProviderError) as info:
        await collect(provider.stream(REQUEST))
    assert info.value.retryable is retryable


async def test_http_stream_malformed_event_is_not_retryable():
    provider = make_http(lambda r: httpx2.Response(200, content=b"data: {not json}\n\n"))
    with pytest.raises(ProviderError) as info:
        await collect(provider.stream(REQUEST))
    assert info.value.retryable is False


async def test_http_stream_connection_failure_is_retryable():
    def handler(request):
        raise httpx2.ConnectError("refused")

    with pytest.raises(ProviderError) as info:
        await collect(make_http(handler).stream(REQUEST))
    assert info.value.retryable is True
