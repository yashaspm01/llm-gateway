import pytest
from fastapi.testclient import TestClient
from helpers import BODY, URL, Scripted, issue_key, make_response

from gateway.app import create_app
from gateway.circuit import CircuitBreaker, CircuitBreakerProvider
from gateway.errors import AllProvidersFailedError, ProviderError
from gateway.failover import FailoverProvider
from gateway.retry import RetryingProvider, RetryPolicy
from gateway.schemas import ChatMessage, ChatRequest

REQUEST = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])


async def test_primary_serves_when_healthy():
    primary = Scripted([make_response()], "primary")
    backup = Scripted([make_response()], "backup")
    reply = await FailoverProvider([primary, backup]).complete(REQUEST)
    assert reply.provider == "primary"
    assert (primary.calls, backup.calls) == (1, 0)


async def test_falls_over_to_the_backup():
    primary = Scripted([ProviderError("down")], "primary")
    backup = Scripted([make_response()], "backup")
    reply = await FailoverProvider([primary, backup]).complete(REQUEST)
    assert reply.provider == "backup"


async def test_non_retryable_errors_also_fail_over():
    primary = Scripted([ProviderError("key revoked", retryable=False)], "primary")
    backup = Scripted([make_response()], "backup")
    reply = await FailoverProvider([primary, backup]).complete(REQUEST)
    assert reply.provider == "backup"


async def test_all_providers_failing_raises_a_503_error():
    a = Scripted([ProviderError("HTTP 503")], "a")
    b = Scripted([ProviderError("HTTP 500")], "b")
    with pytest.raises(AllProvidersFailedError) as info:
        await FailoverProvider([a, b]).complete(REQUEST)
    assert info.value.status_code == 503
    assert "a: HTTP 503" in info.value.message
    assert "b: HTTP 500" in info.value.message


async def test_retry_after_uses_the_smallest_hint():
    a = Scripted([ProviderError("x", retry_after_s=5.0)], "a")
    b = Scripted([ProviderError("y", retry_after_s=2.0)], "b")
    with pytest.raises(AllProvidersFailedError) as info:
        await FailoverProvider([a, b]).complete(REQUEST)
    assert info.value.headers == {"Retry-After": "2"}


async def test_unexpected_exceptions_propagate_without_failover():
    primary = Scripted([ValueError("a bug in our code")], "primary")
    backup = Scripted([make_response()], "backup")
    with pytest.raises(ValueError):
        await FailoverProvider([primary, backup]).complete(REQUEST)
    assert backup.calls == 0


def test_an_empty_list_is_rejected():
    with pytest.raises(ValueError):
        FailoverProvider([])


def test_gateway_fails_over_and_names_the_provider(settings):
    provider = FailoverProvider(
        [Scripted([ProviderError("down")], "primary"), Scripted([make_response()], "backup")]
    )
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        response = client.post(URL, json=BODY, headers=issue_key(client))
    assert response.status_code == 200
    assert response.headers["x-gateway-provider"] == "backup"
    assert "provider" not in response.json()  # internal field, never sent to clients


def test_gateway_returns_503_when_every_provider_fails(settings):
    provider = FailoverProvider(
        [Scripted([ProviderError("x")], "a"), Scripted([ProviderError("y")], "b")]
    )
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        response = client.post(URL, json=BODY, headers=issue_key(client))
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"


async def test_full_stack_stops_calling_a_dead_provider():
    dead = Scripted([ProviderError("HTTP 503")] * 10, "primary")
    healthy = Scripted([make_response() for _ in range(10)], "backup")
    policy = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)
    primary_stack = RetryingProvider(
        CircuitBreakerProvider(dead, CircuitBreaker("primary", failure_threshold=2)), policy
    )
    router = FailoverProvider([primary_stack, healthy])

    first = await router.complete(REQUEST)
    assert first.provider == "backup"
    assert (
        dead.calls == 2
    )  # two failed attempts opened the circuit; the third never left the building

    second = await router.complete(REQUEST)
    assert second.provider == "backup"
    assert dead.calls == 2  # the open circuit skipped the dead provider entirely
    assert healthy.calls == 2
