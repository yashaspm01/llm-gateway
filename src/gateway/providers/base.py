"""The contract every provider must satisfy."""

from collections.abc import AsyncIterator
from typing import Protocol

from gateway.schemas import ChatChunk, ChatRequest, ChatResponse


class Provider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Return a completion, or raise a GatewayError subclass."""
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Yield chunks. Failures raise a ProviderError, possibly after some chunks."""
        ...
