import pytest
from fastapi.testclient import TestClient
from helpers import BODY, URL, issue_key

from gateway.app import create_app
from gateway.errors import ProviderError
from gateway.providers.mock import MockProvider


def payload(**overrides: object) -> dict[str, object]:
    return {**BODY, **overrides}


def test_chat_happy_path(client, auth):
    response = client.post(URL, json=payload(), headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-1"
    assert body["id"].startswith("chatcmpl-")
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert "You said: hello world" in choice["message"]["content"]
    assert choice["finish_reason"] == "stop"


def test_usage_adds_up(client, auth):
    usage = client.post(URL, json=payload(), headers=auth).json()["usage"]
    assert usage["prompt_tokens"] == 2
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_provider_is_called_once(client, provider, auth):
    client.post(URL, json=payload(), headers=auth)
    assert provider.calls == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"model": "m"},
        payload(messages=[]),
        payload(messages=[{"role": "robot", "content": "hi"}]),
        payload(max_tokens=-5),
        payload(temperature=3.5),
    ],
    ids=["no-messages", "empty-messages", "bad-role", "negative-max-tokens", "hot-temperature"],
)
def test_invalid_requests_return_400(client, provider, auth, bad):
    response = client.post(URL, json=bad, headers=auth)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["details"]
    assert provider.calls == 0  # bad input must never reach a provider


def test_max_tokens_truncates_the_reply(client, auth):
    body = client.post(URL, json=payload(max_tokens=2), headers=auth).json()
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["usage"]["completion_tokens"] == 2
    assert body["choices"][0]["message"]["content"] == "[mock] You"


def test_provider_error_maps_to_502(settings):
    app = create_app(
        provider=MockProvider(failure=ProviderError("upstream exploded")), settings=settings
    )
    with TestClient(app) as client:
        response = client.post(URL, json=payload(), headers=issue_key(client))
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "provider_error"


def test_slow_provider_maps_to_504(settings):
    app = create_app(
        provider=MockProvider(latency_s=0.5),
        settings=settings.model_copy(update={"provider_timeout_s": 0.05}),
    )
    with TestClient(app) as client:
        response = client.post(URL, json=payload(), headers=issue_key(client))
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "provider_timeout"
