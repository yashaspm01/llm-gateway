import pytest
from fastapi.testclient import TestClient
from helpers import ADMIN, BODY, URL, Scripted, create_key, create_team, issue_key

from gateway.app import create_app
from gateway.circuit import CircuitBreaker, CircuitBreakerProvider
from gateway.errors import ProviderError


def test_admin_api_is_off_without_a_token(settings):
    app = create_app(settings=settings.model_copy(update={"admin_token": None}))
    with TestClient(app) as client:
        response = client.post("/admin/teams", json={"name": "x"}, headers=ADMIN)
    assert response.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"x-admin-token": "nope"}], ids=["missing", "wrong"])
def test_admin_requires_the_token(client, headers):
    response = client.post("/admin/teams", json={"name": "x"}, headers=headers)
    assert response.status_code == 401


def test_create_team(client):
    response = client.post("/admin/teams", json={"name": "acme"}, headers=ADMIN)
    assert response.status_code == 201
    assert response.json()["name"] == "acme"


def test_duplicate_team_name_is_409(client):
    create_team(client, "acme")
    response = client.post("/admin/teams", json={"name": "acme"}, headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "conflict_error"


def test_key_is_shown_once_with_a_prefix(client):
    created = create_key(client, create_team(client))
    assert created["api_key"].startswith("gw_")
    assert created["api_key"].startswith(created["prefix"])
    assert "key_hash" not in created


def test_unknown_team_and_key_are_404(client):
    assert client.post("/admin/teams/999/keys", headers=ADMIN).status_code == 404
    assert client.delete("/admin/keys/999", headers=ADMIN).status_code == 404


def test_provider_status_for_the_mock(client):
    response = client.get("/admin/providers", headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == [{"name": "mock", "state": "n/a"}]


def test_provider_status_requires_the_admin_token(client):
    assert client.get("/admin/providers").status_code == 401


def test_provider_status_shows_an_open_circuit(settings):
    stack = CircuitBreakerProvider(
        Scripted([ProviderError("x")], "p"), CircuitBreaker("p", failure_threshold=1)
    )
    with TestClient(create_app(provider=stack, settings=settings)) as client:
        assert client.post(URL, json=BODY, headers=issue_key(client)).status_code == 502
        rows = client.get("/admin/providers", headers=ADMIN).json()
    assert rows[0]["name"] == "p"
    assert rows[0]["state"] == "open"
