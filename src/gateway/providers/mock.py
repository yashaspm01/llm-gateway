"""A fake provider for development and tests. It can be slow or fail on demand."""

import asyncio
import time
from uuid import uuid4

from gateway.schemas import ChatMessage, ChatRequest, ChatResponse, Choice, Usage


class MockProvider:
    def __init__(
        self,
        name: str = "mock",
        latency_s: float = 0.0,
        failure: Exception | None = None,
    ) -> None:
        self.name = name
        self.latency_s = latency_s
        self.failure = failure
        self.calls = 0

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.failure is not None:
            raise self.failure

        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        words = f"[{self.name}] You said: {last_user}".split()
        finish_reason = "stop"
        if request.max_tokens is not None and len(words) > request.max_tokens:
            words = words[: request.max_tokens]
            finish_reason = "length"

        # Word counts stand in for real token counts. Real providers report usage themselves.
        prompt_tokens = sum(len(m.content.split()) for m in request.messages)
        completion_tokens = len(words)
        return ChatResponse(
            id=f"chatcmpl-{uuid4().hex}",
            created=int(time.time()),
            model=request.model,
            provider=self.name,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=" ".join(words)),
                    finish_reason="stop" if finish_reason == "stop" else "length",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )
