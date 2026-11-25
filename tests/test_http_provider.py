import json

import httpx2
import pytest

from gateway.errors import ProviderError, ProviderTimeoutError
from gateway.providers.http import OpenAICompatibleProvider, parse_retry_after
from gateway.retry import RetryingProvider, RetryPolicy
from gateway.schemas import ChatMessage, ChatRequest

BASE = "https://upstream.example/v1/"
REQUEST = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hello")])


def good_body(finish_reason: str = "stop") -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "system_fingerprint": "fp_123",  # a field our schema doesn't know about
    }


def make(handler, api_key="sk-secret", model=None):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), base_url=BASE)
    return OpenAICompatibleProvider(
        name="up", base_url=BASE, api_key=api_key, model=model, client=client
    )


async def test_success_sends_the_right_request():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=good_body())

    reply = await make(handler).complete(REQUEST)
    assert seen["url"] == "https://upstream.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-secret"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert reply.choices[0].message.content == "hi"
    assert reply.provider == "up"


async def test_model_override_replaces_the_clients_model_name():
    seen = {}

    def handler(request):
        seen["model"] = json.loads(request.content)["model"]
        return httpx2.Response(200, json=good_body())

    await make(handler, model="provider-specific-model").complete(REQUEST)
    assert seen["model"] == "provider-specific-model"


async def test_unknown_finish_reasons_and_extra_fields_are_tolerated():
    provider = make(lambda r: httpx2.Response(200, json=good_body("content_filter")))
    reply = await provider.complete(REQUEST)
    assert reply.choices[0].finish_reason == "content_filter"


async def test_429_is_retryable_and_carries_retry_after():
    provider = make(lambda r: httpx2.Response(429, headers={"retry-after": "3"}))
    with pytest.raises(ProviderError) as info:
        await provider.complete(REQUEST)
    assert type(info.value) is ProviderError
    assert info.value.retryable is True
    assert info.value.retry_after_s == 3.0


async def test_5xx_is_retryable():
    provider = make(lambda r: httpx2.Response(503))
    with pytest.raises(ProviderError) as info:
        await provider.complete(REQUEST)
    assert info.value.retryable is True
    assert info.value.retry_after_s is None


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_are_not_retryable(status):
    provider = make(lambda r: httpx2.Response(status))
    with pytest.raises(ProviderError) as info:
        await provider.complete(REQUEST)
    assert info.value.retryable is False


async def test_timeout_maps_to_provider_timeout():
    def handler(request):
        raise httpx2.ReadTimeout("too slow")

    with pytest.raises(ProviderTimeoutError):
        await make(handler).complete(REQUEST)


async def test_connection_failure_is_a_retryable_provider_error():
    def handler(request):
        raise httpx2.ConnectError("refused")

    with pytest.raises(ProviderError) as info:
        await make(handler).complete(REQUEST)
    assert type(info.value) is ProviderError
    assert info.value.retryable is True


@pytest.mark.parametrize(
    "kwargs", [{"text": "not json"}, {"json": {"hello": "world"}}], ids=["bad-json", "wrong-shape"]
)
async def test_malformed_responses_are_not_retryable(kwargs):
    provider = make(lambda r: httpx2.Response(200, **kwargs))
    with pytest.raises(ProviderError) as info:
        await provider.complete(REQUEST)
    assert info.value.retryable is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3", 3.0),
        ("0", 0.0),
        ("-1", None),
        ("abc", None),
        (None, None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
    ],
)
def test_parse_retry_after(value, expected):
    assert parse_retry_after(value) == expected


async def test_error_messages_do_not_leak_upstream_details_or_keys():
    provider = make(lambda r: httpx2.Response(401, text="invalid key sk-secret"))
    with pytest.raises(ProviderError) as info:
        await provider.complete(REQUEST)
    assert "sk-secret" not in info.value.message
    assert "invalid key" not in info.value.message


async def test_flaky_upstream_is_retried_end_to_end():
    responses = [httpx2.Response(503), httpx2.Response(503), httpx2.Response(200, json=good_body())]
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return responses[len(calls) - 1]

    async def no_sleep(seconds: float) -> None:
        pass

    provider = RetryingProvider(
        make(handler),
        RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0),
        sleep=no_sleep,
    )
    reply = await provider.complete(REQUEST)
    assert reply.choices[0].message.content == "hi"
    assert len(calls) == 3
