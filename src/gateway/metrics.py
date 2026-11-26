"""Prometheus metrics. Each app gets its own registry, so tests never see each other's numbers."""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


class Metrics:
    content_type = CONTENT_TYPE_LATEST

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._http_requests = Counter(
            "gateway_http_requests_total",
            "HTTP requests",
            ["method", "path", "status"],
            registry=self.registry,
        )
        self._http_duration = Histogram(
            "gateway_http_request_duration_seconds",
            "Time until the response headers are ready (first byte for streams)",
            ["method", "path"],
            buckets=BUCKETS,
            registry=self.registry,
        )
        self._chat_requests = Counter(
            "gateway_chat_requests_total",
            "Completed chat requests by serving provider",
            ["provider", "stream"],
            registry=self.registry,
        )
        self._tokens = Counter(
            "gateway_tokens_total",
            "Tokens served, by team and kind",
            ["team", "kind"],
            registry=self.registry,
        )
        self._circuit_open = Gauge(
            "gateway_circuit_open",
            "1 while a provider's circuit is open",
            ["provider"],
            registry=self.registry,
        )

    def observe_http(self, method: str, path: str, status: int, seconds: float) -> None:
        self._http_requests.labels(method=method, path=path, status=str(status)).inc()
        self._http_duration.labels(method=method, path=path).observe(seconds)

    def observe_chat(
        self,
        *,
        provider: str,
        stream: bool,
        team_id: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        self._chat_requests.labels(provider=provider, stream=str(stream).lower()).inc()
        self._tokens.labels(team=str(team_id), kind="prompt").inc(prompt_tokens)
        self._tokens.labels(team=str(team_id), kind="completion").inc(completion_tokens)

    def update_circuits(self, rows: list[dict[str, object]]) -> None:
        for row in rows:
            state = row.get("state")
            if state in ("open", "half_open", "closed"):
                self._circuit_open.labels(provider=str(row["name"])).set(
                    1.0 if state == "open" else 0.0
                )

    def render(self) -> bytes:
        return generate_latest(self.registry)
