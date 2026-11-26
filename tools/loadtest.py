"""A small load generator.

    LOADTEST_KEY=gw_... python tools/loadtest.py --url http://127.0.0.1:8000 \
        --requests 200 --concurrency 20 [--stream]
"""

import argparse
import asyncio
import os
import time
from collections import Counter

import httpx2

BODY = {"model": "any", "messages": [{"role": "user", "content": "hello from the load test"}]}


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = round(pct / 100 * (len(sorted_values) - 1))
    return sorted_values[min(len(sorted_values) - 1, max(0, index))]


async def call(
    client: httpx2.AsyncClient, headers: dict[str, str], stream: bool
) -> tuple[int, float, float | None]:
    """Returns (status, total seconds, seconds to first streamed chunk)."""
    start = time.perf_counter()
    first_chunk: float | None = None
    status = 0
    try:
        if stream:
            async with client.stream(
                "POST", "/v1/chat/completions", json={**BODY, "stream": True}, headers=headers
            ) as response:
                status = response.status_code
                async for line in response.aiter_lines():
                    if first_chunk is None and line.startswith("data:"):
                        first_chunk = time.perf_counter() - start
        else:
            response = await client.post("/v1/chat/completions", json=BODY, headers=headers)
            status = response.status_code
    except httpx2.HTTPError:
        status = 0  # connection-level failure
    return status, time.perf_counter() - start, first_chunk


async def main() -> None:
    parser = argparse.ArgumentParser(description="Load-test the gateway.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("LOADTEST_KEY")
    if not key:
        raise SystemExit("Set LOADTEST_KEY to an API key first.")
    headers = {"authorization": f"Bearer {key}"}

    gate = asyncio.Semaphore(args.concurrency)
    results: list[tuple[int, float, float | None]] = []
    limits = httpx2.Limits(max_connections=args.concurrency)

    async with httpx2.AsyncClient(base_url=args.url, timeout=60, limits=limits) as client:

        async def one() -> None:
            async with gate:
                results.append(await call(client, headers, args.stream))

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(args.requests)))
        elapsed = time.perf_counter() - started

    statuses = Counter(status for status, _, _ in results)
    latencies = sorted(seconds for status, seconds, _ in results if status == 200)
    ok = statuses.get(200, 0)
    print(f"requests: {len(results)}  concurrency: {args.concurrency}  stream: {args.stream}")
    print(f"duration: {elapsed:.2f}s  throughput: {len(results) / elapsed:.1f} req/s")
    print(f"status codes: {dict(sorted(statuses.items()))}  (0 = connection error)")
    print(f"success: {ok / len(results):.1%}")
    if latencies:
        print(
            "latency (200s only, ms): "
            + "  ".join(f"p{p}={percentile(latencies, p) * 1000:.0f}" for p in (50, 95, 99))
            + f"  max={latencies[-1] * 1000:.0f}"
        )
    firsts = sorted(f for status, _, f in results if status == 200 and f is not None)
    if firsts:
        print(
            "time to first chunk (ms): "
            f"p50={percentile(firsts, 50) * 1000:.0f}  p95={percentile(firsts, 95) * 1000:.0f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
