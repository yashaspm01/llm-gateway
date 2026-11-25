"""Shared test helpers. Setup goes through the real admin API, so every test is end to end."""

from typing import Any

from fastapi.testclient import TestClient

from gateway.schemas import ChatMessage, ChatResponse, Choice, Usage

ADMIN = {"x-admin-token": "admin-secret"}
URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hello world"}]}


def create_team(client: TestClient, name: str = "acme", **limits: Any) -> int:
    response = client.post("/admin/teams", json={"name": name, **limits}, headers=ADMIN)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def create_key(client: TestClient, team_id: int) -> dict[str, Any]:
    response = client.post(f"/admin/teams/{team_id}/keys", headers=ADMIN)
    assert response.status_code == 201, response.text
    return dict(response.json())


def bearer(created: dict[str, Any]) -> dict[str, str]:
    return {"authorization": f"Bearer {created['api_key']}"}


def issue_key(client: TestClient, team: str = "acme", **limits: Any) -> dict[str, str]:
    return bearer(create_key(client, create_team(client, team, **limits)))


def make_response(content: str = "hi") -> ChatResponse:
    return ChatResponse(
        id="chatcmpl-test",
        created=1,
        model="mock-1",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class Scripted:
    """A provider that plays back fixed outcomes: exceptions are raised, anything else returned."""

    def __init__(self, outcomes: list[object]) -> None:
        self.name = "scripted"
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete(self, request: object) -> ChatResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]
