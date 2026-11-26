"""A fake provider for development and tests. It can be slow or fail on demand."""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import uuid4

from gateway.schemas import (
    ChatChunk,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Choice,
    ChunkChoice,
    ChunkDelta,
    Usage,
)


def _prompt_tokens(request: ChatRequest) -> int:
    # Word counts stand in for real token counts. Real providers report usage themselves.
    return sum(len(m.content.split()) for m in request.messages)


class MockProvider:
    def __init__(
        self,
        name: str = "mock",
        latency_s: float = 0.0,
        failure: Exception | None = None,
        fail_after_chunks: int | None = None,
        chunk_delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self.latency_s = latency_s
        self.failure = failure
        self.fail_after_chunks = fail_after_chunks  # streaming: fail after this many words
        self.chunk_delay_s = chunk_delay_s
        self.calls = 0

    def _reply(self, request: ChatRequest) -> tuple[list[str], str]:
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        words = f"[{self.name}] You said: {last_user}".split()
        finish_reason = "stop"
        if request.max_tokens is not None and len(words) > request.max_tokens:
            words = words[: request.max_tokens]
            finish_reason = "length"
        return words, finish_reason

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.failure is not None:
            raise self.failure

        words, finish_reason = self._reply(request)
        prompt_tokens = _prompt_tokens(request)
        return ChatResponse(
            id=f"chatcmpl-{uuid4().hex}",
            created=int(time.time()),
            model=request.model,
            provider=self.name,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=" ".join(words)),
                    finish_reason=finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=len(words),
                total_tokens=prompt_tokens + len(words),
            ),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.failure is not None and self.fail_after_chunks is None:
            raise self.failure  # fails before the first chunk

        words, finish_reason = self._reply(request)
        stream_id = f"chatcmpl-{uuid4().hex}"
        created = int(time.time())

        def make_chunk(
            delta: ChunkDelta, finish: str | None = None, usage: Usage | None = None
        ) -> ChatChunk:
            return ChatChunk(
                id=stream_id,
                created=created,
                model=request.model,
                choices=[ChunkChoice(delta=delta, finish_reason=finish)],
                usage=usage,
                provider=self.name,
            )

        yield make_chunk(ChunkDelta(role="assistant", content=""))
        for index, word in enumerate(words):
            if self.failure is not None and self.fail_after_chunks == index:
                raise self.failure  # fails mid-stream
            if self.chunk_delay_s:
                await asyncio.sleep(self.chunk_delay_s)
            yield make_chunk(ChunkDelta(content=word if index == 0 else f" {word}"))
        if self.failure is not None:
            raise self.failure  # asked to fail after more chunks than the reply has

        prompt_tokens = _prompt_tokens(request)
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=len(words),
            total_tokens=prompt_tokens + len(words),
        )
        yield make_chunk(ChunkDelta(), finish_reason, usage)
