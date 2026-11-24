"""Shared test helpers. Setup goes through the real admin API, so every test is end to end."""

from typing import Any

from fastapi.testclient import TestClient

ADMIN = {"x-admin-token": "admin-secret"}
URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hello world"}]}


def create_team(client: TestClient, name: str = "acme") -> int:
    response = client.post("/admin/teams", json={"name": name}, headers=ADMIN)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def create_key(client: TestClient, team_id: int) -> dict[str, Any]:
    response = client.post(f"/admin/teams/{team_id}/keys", headers=ADMIN)
    assert response.status_code == 201, response.text
    return dict(response.json())


def bearer(created: dict[str, Any]) -> dict[str, str]:
    return {"authorization": f"Bearer {created['api_key']}"}


def issue_key(client: TestClient, team: str = "acme") -> dict[str, str]:
    return bearer(create_key(client, create_team(client, team)))
