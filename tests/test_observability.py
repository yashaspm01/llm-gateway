import json
import logging
import re
import sys

from fastapi.testclient import TestClient
from helpers import ADMIN, BODY, URL, Scripted, issue_key

from gateway.app import create_app
from gateway.circuit import CircuitBreaker, CircuitBreakerProvider
from gateway.errors import ProviderError
from gateway.logging_setup import JsonFormatter, request_id_var


def sample(text: str, name: str, **labels: str) -> float | None:
    """The value of one Prometheus sample, found by metric name and labels."""
    for line in text.splitlines():
        if not (line.startswith(name + "{") or line.startswith(name + " ")):
            continue
        if all(f'{key}="{value}"' in line for key, value in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return None


def make_record(message="hello", exc_info=None):
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, (), exc_info)


def test_json_formatter_includes_request_id_and_extras():
    record = make_record()
    record.status = 200
    token = request_id_var.set("req-123")
    try:
        data = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert data["message"] == "hello"
    assert data["level"] == "INFO"
    assert data["request_id"] == "req-123"
    assert data["status"] == 200
    assert data["ts"].endswith("+00:00")


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record("failed", sys.exc_info())
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in data["exception"]


def test_access_log_has_structured_fields(client, auth, caplog):
    with caplog.at_level(logging.INFO, logger="gateway.access"):
        client.post(URL, json=BODY, headers=auth)
    record = next(r for r in caplog.records if r.name == "gateway.access" and r.path == URL)
    assert (record.method, record.status) == ("POST", 200)
    assert record.team_id is not None
    assert record.duration_ms >= 0


def test_unsafe_request_ids_are_replaced(client):
    supplied = "x" * 200
    returned = client.get("/health", headers={"x-request-id": supplied}).headers["x-request-id"]
    assert returned != supplied
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", returned)


def test_metrics_require_the_admin_token(client):
    assert client.get("/metrics").status_code == 401


def test_metrics_are_off_without_an_admin_token(settings):
    app = create_app(settings=settings.model_copy(update={"admin_token": None}))
    with TestClient(app) as client:
        assert client.get("/metrics", headers=ADMIN).status_code == 404


def test_metrics_count_requests_and_tokens(client, auth):
    client.post(URL, json=BODY, headers=auth)
    text = client.get("/metrics", headers=ADMIN).text
    route = {"method": "POST", "path": "/v1/chat/completions"}
    assert sample(text, "gateway_http_requests_total", status="200", **route) == 1.0
    assert sample(text, "gateway_http_request_duration_seconds_count", **route) == 1.0
    assert sample(text, "gateway_tokens_total", team="1", kind="prompt") == 2.0
    assert sample(text, "gateway_tokens_total", team="1", kind="completion") == 5.0
    assert sample(text, "gateway_chat_requests_total", provider="mock", stream="false") == 1.0


def test_unknown_paths_share_one_label(client):
    client.get("/nope-1")
    client.get("/nope-2")
    text = client.get("/metrics", headers=ADMIN).text
    assert sample(text, "gateway_http_requests_total", path="unmatched", status="404") == 2.0
    assert "/nope" not in text


def test_circuit_state_is_exported(settings):
    stack = CircuitBreakerProvider(
        Scripted([ProviderError("x")], "p"), CircuitBreaker("p", failure_threshold=1)
    )
    with TestClient(create_app(provider=stack, settings=settings)) as client:
        client.post(URL, json=BODY, headers=issue_key(client))
        text = client.get("/metrics", headers=ADMIN).text
    assert sample(text, "gateway_circuit_open", provider="p") == 1.0


def test_streamed_requests_are_labelled_as_streams(client, auth):
    client.post(URL, json={**BODY, "stream": True}, headers=auth)
    text = client.get("/metrics", headers=ADMIN).text
    assert sample(text, "gateway_chat_requests_total", provider="mock", stream="true") == 1.0
