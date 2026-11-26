# LLM Gateway

[![CI](https://github.com/yashaspm01/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/yashaspm01/llm-gateway/actions/workflows/ci.yml)

An OpenAI-compatible gateway that sits in front of LLM providers and adds what raw API
calls lack: team API keys, rate limits, daily token budgets, retries, circuit breakers,
provider failover, streaming, structured logs and metrics.

**Live demo:** https://llm-gateway-d8qh.onrender.com/health
(free tier with a mock provider; the first request after idle is slow while it wakes up)

## Why

Calling an LLM API directly is fragile and uncontrolled: providers rate-limit, time out
and go down, costs are unbounded, and every team needs its own access. This service puts
one reliable, observable entry point in front of them. Existing OpenAI clients work by
changing only the base URL.

## Architecture

```
client ─► auth ─► rate limit ─► budget ─► Failover ─► Retry ─► CircuitBreaker ─► HTTP provider
            │                                │                                      │
       API keys                        next provider                         any OpenAI-
     (hashed in DB)                    if one fails                         compatible API
```

Each configured provider becomes `Retry(CircuitBreaker(Http))`, and several providers are
combined by `Failover`. The breaker sits inside the retry layer so it sees every attempt
and opens quickly, and an open circuit stops further retries.

## Features

- **Auth:** per-team API keys, stored as keyed hashes (HMAC-SHA256), shown once, revocable.
- **Limits:** token-bucket rate limit per team (429 with `Retry-After`) and a daily token budget.
- **Reliability:** retries with exponential backoff and full jitter that honor `Retry-After`;
  a circuit breaker per provider (closed, open, half-open); ordered failover; one total deadline.
- **Streaming:** Server-Sent Events passthrough. Retries and failover happen before the
  first chunk; a failure after that is reported as an error event.
- **Observability:** JSON logs with a request ID on every line, Prometheus metrics at
  `/metrics`, provider and circuit health at `/admin/providers`.
- **Operations:** Alembic migrations (tested against Postgres in CI), a non-root Docker
  image with a health check, and a free deployment on Render with managed Postgres.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
GATEWAY_ADMIN_TOKEN=local-admin uvicorn gateway.main:app
```

Create a team and a key, then call the gateway:

```bash
curl -X POST localhost:8000/admin/teams -H 'x-admin-token: local-admin' \
  -H 'content-type: application/json' -d '{"name":"acme"}'
curl -X POST localhost:8000/admin/teams/1/keys -H 'x-admin-token: local-admin'   # the key is shown once

curl localhost:8000/v1/chat/completions -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"any","messages":[{"role":"user","content":"hello"}]}'
```

Add `"stream": true` to receive Server-Sent Events. Without configuration the gateway uses
a built-in mock provider, so nothing here costs money.

To try failover and retries locally, run the fake upstream and point the gateway at it:

```bash
FAIL_FIRST=2 FAIL_STATUS=503 uvicorn tools.fake_upstream:app --port 9000
GATEWAY_PROVIDER=openai_compatible GATEWAY_PROVIDER_BASE_URL=http://127.0.0.1:9000/v1 \
  GATEWAY_ADMIN_TOKEN=local-admin uvicorn gateway.main:app
```

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /v1/chat/completions` | team key | chat completion, streaming optional |
| `GET /health` | none | liveness |
| `GET /metrics` | admin token | Prometheus metrics |
| `POST /admin/teams`, `PATCH /admin/teams/{id}` | admin token | create a team; change its limits |
| `POST /admin/teams/{id}/keys`, `DELETE /admin/keys/{id}` | admin token | issue and revoke keys |
| `GET /admin/teams/{id}/usage` | admin token | token and request totals |
| `GET /admin/providers` | admin token | provider and circuit-breaker state |

| Status | Meaning |
|---|---|
| 400 | invalid request |
| 401 | missing or invalid key |
| 429 | rate limit or daily budget exceeded (see `Retry-After`) |
| 502 / 504 | a provider failed / timed out |
| 503 | a circuit is open, or every provider failed |

Responses carry an `x-gateway-provider` header naming the provider that served the request,
and an `x-request-id` header that also appears on every log line for that request.

## Configuration

All settings are environment variables with the prefix `GATEWAY_`.

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `dev`, `test` or `prod` (prod refuses to start without a real `API_KEY_PEPPER`) |
| `ADMIN_TOKEN` | enables the admin API and `/metrics`; unset means they return 404 |
| `API_KEY_PEPPER` | secret used when hashing API keys; never change it once keys exist |
| `DATABASE_URL` | SQLite by default; `postgres://` and `postgresql://` URLs are normalized for asyncpg |
| `PROVIDER`, `PROVIDER_NAME`, `PROVIDER_BASE_URL`, `PROVIDER_API_KEY` | a single upstream (`mock` or `openai_compatible`) |
| `PROVIDERS_FILE` | JSON list of upstreams for failover (see `providers.example.json`); it names environment variables for keys and never contains secrets |
| `PROVIDER_TIMEOUT_S`, `PROVIDER_ATTEMPT_TIMEOUT_S` | total deadline (time to first chunk for streams) and per-attempt timeout |
| `RETRY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY_S`, `RETRY_MAX_DELAY_S` | retry policy |
| `BREAKER_FAILURE_THRESHOLD`, `BREAKER_RECOVERY_TIMEOUT_S` | circuit breaker |
| `LOG_LEVEL`, `LOG_FORMAT` | `text` or `json` (the Docker image defaults to `json`) |

## Testing

```bash
pytest -q && ruff check . && ruff format --check . && mypy
```

177 tests, strict typing and lint run in CI on every push. CI also applies the migrations
to a real Postgres service (`upgrade`, `check`, `downgrade`, `upgrade`) and smoke-tests the
built container. Failure paths are tested with scripted and fake providers: timeouts,
429s, 5xx responses and mid-stream crashes. Time-dependent logic (rate limiter, circuit
breaker, retry backoff) uses injected clocks and sleeps, so those tests run in milliseconds.
`tools/fake_upstream.py` is a local upstream that fails on demand.

## Load test

Setup: one uvicorn worker, SQLite, JSON access logging on, and a local fake upstream
(`tools/fake_upstream.py` with `SLOW_S=0.05 CHUNK_S=0.01`) that adds about 50 ms per request
and 10 ms per streamed word. Machine: AMD Ryzen 9 8940HX, 7.4 GiB RAM, Linux (Ubuntu).
Each scenario was run once, against a server that had already handled a request.

| Scenario | Throughput | p50 | p95 | p99 | Time to first chunk (p50 / p95) | Success |
|---|---|---|---|---|---|---|
| Non-streaming, 200 requests, concurrency 20 | 181 req/s | 101 ms | 136 ms | 148 ms | n/a | 100% |
| Streaming, 100 requests, concurrency 20 | 128 req/s | 147 ms | 167 ms | 175 ms | 81 ms / 91 ms | 100% |

These numbers describe the gateway's own overhead against a fake upstream on one machine;
they are not a production capacity claim. Throughput tops out near 180 req/s on one worker,
and most latency beyond the upstream's simulated delay is probably queueing in that single
process, since every request does an auth lookup and a usage write (not profiled). The load
generator is closed-loop (a new request starts when one finishes), which can understate tail
latency under a real traffic spike.

## Deployment

```bash
docker build -t llm-gateway .
docker run --rm -p 8000:8000 -e GATEWAY_ADMIN_TOKEN=local-admin llm-gateway
```

The image runs migrations on start, as a non-root user, with JSON logs and a health check.
CI builds it on every push, and tags like `v0.1.0` publish it to GitHub's container registry.

The live demo runs on Render's free tier against a free managed Postgres database. Teams,
keys and usage live in the database, so they survive redeploys. In-memory state (rate-limit
buckets, circuit-breaker state, metrics) resets whenever the free instance suspends or
restarts. This is a demo deployment, not a service with an availability guarantee.

## Design decisions

- **Breaker inside retry:** it sees every attempt and opens quickly; once open, retries stop.
- **Keyed hash instead of bcrypt for API keys:** keys are 256-bit random values, so slow
  hashing would add latency to every request without adding security.
- **Streams are retried or failed over only before the first chunk:** after that, headers
  and bytes have gone to the client and cannot be taken back.
- **Injected clocks, sleeps and random generators:** time-dependent logic is tested
  deterministically and in milliseconds.
- **Metric labels use route templates:** unknown URLs cannot create unbounded series.
- **Request IDs from clients are validated** before they reach logs and headers.
- **Prompts, API keys and upstream error bodies are never logged.**
- **Config names environment variables, never secrets:** `providers.json` is safe to commit.

## Known limitations

- Rate-limit buckets, breaker state and metrics live in process memory: one replica only,
  and a restart resets them. Scaling out needs a shared store such as Redis.
- Daily budgets are soft: concurrent requests can overshoot slightly. They reset at
  midnight UTC.
- Usage for streams whose client disconnects is best-effort, and token counts are estimated
  when a provider does not report usage.
- Text chat only (no tool calls or multimodal content) and one HTTP provider type. Streamed
  chunks include explicit `null` fields, which OpenAI's own chunks omit.
- Migrations run at container start, which suits one instance but not several.
- The circuit breaker counts consecutive failures, not an error rate over a time window.

## Project layout

| Path | Purpose |
|---|---|
| `src/gateway/app.py`, `main.py` | application factory and entry point |
| `src/gateway/admin.py`, `deps.py`, `services.py` | admin API, request dependencies, database operations |
| `src/gateway/ratelimit.py`, `retry.py`, `circuit.py`, `failover.py` | the reliability layers |
| `src/gateway/providers/` | provider interface, mock, HTTP client, factory, config file loader |
| `src/gateway/logging_setup.py`, `metrics.py`, `status.py` | observability |
| `src/gateway/config.py`, `models.py`, `db.py`, `security.py`, `schemas.py`, `errors.py` | settings, tables, hashing, request shapes |
| `alembic/` | database migrations |
| `tools/` | fake upstream and load-test script |
| `tests/` | unit, API and failure-injection tests |

## License

MIT
