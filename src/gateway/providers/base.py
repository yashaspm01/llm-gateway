"""The contract every provider must satisfy."""

from typing import Protocol

from gateway.schemas import ChatRequest, ChatResponse


class Provider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Return a completion, or raise a GatewayError subclass."""
        ...
