from fastapi.testclient import TestClient
from helpers import BODY, URL, Scripted, issue_key, make_response

from gateway.app import create_app
from gateway.errors import ProviderError
from gateway.retry import RetryingProvider, RetryPolicy

INSTANT = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)


def call(settings, inner, policy=INSTANT):
    app = create_app(provider=RetryingProvider(inner, policy), settings=settings)
    with TestClient(app) as client:
        return client.post(URL, json=BODY, headers=issue_key(client))


def test_gateway_recovers_from_a_transient_failure(settings):
    inner = Scripted([ProviderError("blip"), make_response()])
    response = call(settings, inner)
    assert response.status_code == 200
    assert inner.calls == 2


def test_gateway_gives_up_with_502_after_all_attempts(settings):
    inner = Scripted([ProviderError("down")] * 3)
    response = call(settings, inner)
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "provider_error"
    assert inner.calls == 3


def test_permanent_errors_are_not_retried(settings):
    inner = Scripted([ProviderError("bad key", retryable=False)])
    response = call(settings, inner)
    assert response.status_code == 502
    assert inner.calls == 1


def test_total_deadline_bounds_all_retries(settings):
    inner = Scripted([ProviderError("x", retry_after_s=3.0)] * 5)
    patience = RetryPolicy(max_attempts=5, max_delay_s=5.0)
    response = call(settings.model_copy(update={"provider_timeout_s": 0.1}), inner, patience)
    assert response.status_code == 504
    assert inner.calls == 1
