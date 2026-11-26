"""A fake OpenAI-compatible upstream that fails on demand.

    FAIL_FIRST=2 FAIL_STATUS=503 uvicorn tools.fake_upstream:app --port 9000

Environment knobs:
    FAIL_FIRST   fail this many requests before behaving (default 2)
    FAIL_STATUS  status code for those failures (default 503)
    SLOW_S       delay before every response (default 0)
    CHUNK_S      delay between streamed words (default 0.05)
    BREAK_AFTER  if > 0, streams crash after this many words (default 0)
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()
state = {"calls": 0}
FAIL_FIRST = int(os.environ.get("FAIL_FIRST", "2"))
FAIL_STATUS = int(os.environ.get("FAIL_STATUS", "503"))
SLOW_S = float(os.environ.get("SLOW_S", "0"))
CHUNK_S = float(os.environ.get("CHUNK_S", "0.05"))
BREAK_AFTER = int(os.environ.get("BREAK_AFTER", "0"))
WORDS = ["hello", "from", "the", "fake", "upstream"]


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/v1/chat/completions", response_model=None)
async def chat(request: Request) -> Response:
    state["calls"] += 1
    if SLOW_S:
        await asyncio.sleep(SLOW_S)
    if state["calls"] <= FAIL_FIRST:
        headers = {"Retry-After": "1"} if FAIL_STATUS == 429 else None
        return JSONResponse(
            {"error": "simulated failure"}, status_code=FAIL_STATUS, headers=headers
        )

    body = await request.json()
    model = body.get("model", "fake")
    created = int(time.time())
    call_id = f"chatcmpl-fake-{state['calls']}"
    usage = {"prompt_tokens": 3, "completion_tokens": len(WORDS), "total_tokens": 3 + len(WORDS)}

    if not body.get("stream"):
        return JSONResponse(
            {
                "id": call_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": " ".join(WORDS)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
        )

    async def events() -> AsyncIterator[str]:
        base = {
            "id": call_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
        }
        role = {"role": "assistant", "content": ""}
        yield _event({**base, "choices": [{"index": 0, "delta": role, "finish_reason": None}]})
        for index, word in enumerate(WORDS):
            if BREAK_AFTER and index >= BREAK_AFTER:
                raise RuntimeError("simulated mid-stream crash")
            await asyncio.sleep(CHUNK_S)
            delta = {"content": word if index == 0 else f" {word}"}
            yield _event({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        yield _event({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield _event({**base, "choices": [], "usage": usage})
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/calls")
async def calls() -> dict[str, int]:
    return state
