"""A fake OpenAI-compatible upstream that fails on demand.

FAIL_FIRST=2 FAIL_STATUS=503 uvicorn tools.fake_upstream:app --port 9000
"""

import asyncio
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
state = {"calls": 0}
FAIL_FIRST = int(os.environ.get("FAIL_FIRST", "2"))
FAIL_STATUS = int(os.environ.get("FAIL_STATUS", "503"))
SLOW_S = float(os.environ.get("SLOW_S", "2"))


@app.post("/v1/chat/completions")
async def chat(request: Request) -> JSONResponse:
    if SLOW_S:
        await asyncio.sleep(SLOW_S)
    state["calls"] += 1
    if state["calls"] <= FAIL_FIRST:
        headers = {"Retry-After": "1"} if FAIL_STATUS == 429 else None
        return JSONResponse(
            {"error": "simulated failure"}, status_code=FAIL_STATUS, headers=headers
        )
    body = await request.json()
    return JSONResponse(
        {
            "id": f"chatcmpl-fake-{state['calls']}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello from the fake upstream"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }
    )


@app.get("/calls")
async def calls() -> dict[str, int]:
    return state
