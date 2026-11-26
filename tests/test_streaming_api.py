import json

from fastapi.testclient import TestClient
from helpers import (
    ADMIN,
    BODY,
    URL,
    Scripted,
    bearer,
    create_key,
    create_team,
    issue_key,
    make_chunks,
)

from gateway.app import create_app
from gateway.errors import ProviderError
from gateway.failover import FailoverProvider
from gateway.providers.mock import MockProvider

STREAM = {**BODY, "stream": True}


def events(response):
    """The data payloads of an SSE response, in order."""
    return [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_returns_sse_events_ending_with_done(client, auth):
    response = client.post(URL, json=STREAM, headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-gateway-provider"] == "mock"
    payloads = events(response)
    assert payloads[-1] == "[DONE]"
    chunks = [json.loads(p) for p in payloads[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "[mock] You said: hello world"
    assert chunks[-1]["usage"]["completion_tokens"] == 5
    assert all("provider" not in c for c in chunks)


def test_stream_usage_is_recorded(client):
    team_id = create_team(client)
    headers = bearer(create_key(client, team_id))
    client.post(URL, json=STREAM, headers=headers)
    usage = client.get(f"/admin/teams/{team_id}/usage", headers=ADMIN).json()
    assert usage["requests"] == 1
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (2, 5)


def test_failure_before_the_first_chunk_is_a_normal_http_error(settings):
    app = create_app(provider=MockProvider(failure=ProviderError("down")), settings=settings)
    with TestClient(app) as client:
        response = client.post(URL, json=STREAM, headers=issue_key(client))
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "provider_error"


def test_mid_stream_failure_sends_an_error_event_and_no_done(settings):
    provider = MockProvider(failure=ProviderError("broke"), fail_after_chunks=2)
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        team_id = create_team(client)
        response = client.post(URL, json=STREAM, headers=bearer(create_key(client, team_id)))
        usage = client.get(f"/admin/teams/{team_id}/usage", headers=ADMIN).json()
    assert response.status_code == 200  # the headers had already gone out
    payloads = events(response)
    assert "[DONE]" not in payloads
    assert json.loads(payloads[-1])["error"]["type"] == "provider_error"
    assert usage["requests"] == 1
    assert usage["completion_tokens"] == 2  # estimated from the two words that were relayed


def test_slow_first_chunk_is_a_504(settings):
    app = create_app(
        provider=MockProvider(latency_s=0.5),
        settings=settings.model_copy(update={"provider_timeout_s": 0.05}),
    )
    with TestClient(app) as client:
        response = client.post(URL, json=STREAM, headers=issue_key(client))
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "provider_timeout"


def test_stream_requires_authentication(client, provider):
    response = client.post(URL, json=STREAM)
    assert response.status_code == 401
    assert provider.calls == 0


def test_stream_fails_over_through_the_gateway(settings):
    provider = FailoverProvider(
        [Scripted([ProviderError("down")], "primary"), Scripted([make_chunks("hi")], "backup")]
    )
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        response = client.post(URL, json=STREAM, headers=issue_key(client))
    assert response.status_code == 200
    assert response.headers["x-gateway-provider"] == "backup"
