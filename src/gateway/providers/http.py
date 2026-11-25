"""A provider that talks to any OpenAI-compatible HTTP API."""

import httpx2

from gateway.errors import ProviderError, ProviderTimeoutError
from gateway.schemas import ChatRequest, ChatResponse


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header. The HTTP-date form is not supported."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 10.0,
        model: str | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        # One client for the life of the app: it keeps connections open and reuses them.
        self._client = client or httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx2.Timeout(timeout_s),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        payload = request.model_dump(exclude_none=True)
        if self._model is not None:
            payload["model"] = self._model
        try:
            response = await self._client.post(
                "chat/completions", json=payload, headers=self._headers
            )
        except httpx2.TimeoutException as exc:  # must come before TransportError
            raise ProviderTimeoutError(f"Provider '{self.name}' timed out") from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                f"Could not reach provider '{self.name}' ({type(exc).__name__})"
            ) from exc

        status = response.status_code
        if status == 429 or status >= 500:
            raise ProviderError(
                f"Provider '{self.name}' returned HTTP {status}",
                retryable=True,
                retry_after_s=parse_retry_after(response.headers.get("retry-after")),
            )
        if status >= 400:
            raise ProviderError(
                f"Provider '{self.name}' rejected the request (HTTP {status})",
                retryable=False,
            )
        try:
            reply = ChatResponse.model_validate(response.json())
        except ValueError as exc:  # bad JSON, or JSON of the wrong shape
            raise ProviderError(
                f"Provider '{self.name}' returned a malformed response", retryable=False
            ) from exc
        reply.provider = self.name
        return reply
