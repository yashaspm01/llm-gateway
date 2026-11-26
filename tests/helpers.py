"""Shared test helpers. Setup goes through the real admin API, so every test is end to end."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi.testclient import TestClient

from gateway.schemas import (
    ChatChunk,
    ChatMessage,
    ChatResponse,
    Choice,
    ChunkChoice,
    ChunkDelta,
    Usage,
)

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


def make_chunks(*words: str) -> list[ChatChunk]:
    """A role chunk, one chunk per word, then a final chunk carrying usage."""

    def chunk(delta: ChunkDelta, finish: str | None = None, usage: Usage | None = None):
        return ChatChunk(
            id="chatcmpl-stream-test",
            created=1,
            model="mock-1",
            choices=[ChunkChoice(delta=delta, finish_reason=finish)],
            usage=usage,
        )

    usage = Usage(prompt_tokens=1, completion_tokens=len(words), total_tokens=1 + len(words))
    return [
        chunk(ChunkDelta(role="assistant", content="")),
        *(chunk(ChunkDelta(content=word)) for word in words),
        chunk(ChunkDelta(), "stop", usage),
    ]


async def collect(stream: AsyncIterator[ChatChunk]) -> list[ChatChunk]:
    return [chunk async for chunk in stream]


class Scripted:
    """A provider that plays back fixed outcomes.

    complete(): an exception is raised, a ChatResponse is returned.
    stream():   an exception is raised before the first chunk; a list is played chunk by
                chunk, and an exception inside the list is raised at that point (mid-stream).
    """

    def __init__(self, outcomes: list[object], name: str = "scripted") -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete(self, request: object) -> ChatResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ChatResponse)
        if outcome.provider is None:
            outcome = outcome.model_copy(update={"provider": self.name})
        return outcome

    async def stream(self, request: object) -> AsyncIterator[ChatChunk]:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, list)
        for item in outcome:
            if isinstance(item, Exception):
                raise item
            if item.provider is None:
                item = item.model_copy(update={"provider": self.name})
            yield item
