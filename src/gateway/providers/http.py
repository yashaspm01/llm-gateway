"""A provider that talks to any OpenAI-compatible HTTP API."""

from collections.abc import AsyncIterator
from typing import Any

import httpx2

from gateway.errors import ProviderError, ProviderTimeoutError
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse


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
        # The timeout applies per read, so it also bounds the silence between streamed chunks.
        self._client = client or httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx2.Timeout(timeout_s),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True)
        if self._model is not None:
            payload["model"] = self._model
        return payload

    def _status_error(self, response: httpx2.Response) -> ProviderError | None:
        status = response.status_code
        if status == 429 or status >= 500:
            return ProviderError(
                f"Provider '{self.name}' returned HTTP {status}",
                retryable=True,
                retry_after_s=parse_retry_after(response.headers.get("retry-after")),
            )
        if status >= 400:
            return ProviderError(
                f"Provider '{self.name}' rejected the request (HTTP {status})",
                retryable=False,
            )
        return None

    async def complete(self, request: ChatRequest) -> ChatResponse:
        try:
            response = await self._client.post(
                "chat/completions", json=self._payload(request), headers=self._headers
            )
        except httpx2.TimeoutException as exc:  # must come before TransportError
            raise ProviderTimeoutError(f"Provider '{self.name}' timed out") from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                f"Could not reach provider '{self.name}' ({type(exc).__name__})"
            ) from exc

        error = self._status_error(response)
        if error is not None:
            raise error
        try:
            reply = ChatResponse.model_validate(response.json())
        except ValueError as exc:  # bad JSON, or JSON of the wrong shape
            raise ProviderError(
                f"Provider '{self.name}' returned a malformed response", retryable=False
            ) from exc
        reply.provider = self.name
        return reply

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        payload = self._payload(request)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}  # ask for token counts at the end
        try:
            async with self._client.stream(
                "POST", "chat/completions", json=payload, headers=self._headers
            ) as response:
                error = self._status_error(response)
                if error is not None:
                    raise error
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank separators and ": keep-alive" comments
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = ChatChunk.model_validate_json(data)
                    except ValueError as exc:
                        raise ProviderError(
                            f"Provider '{self.name}' sent a malformed stream event",
                            retryable=False,
                        ) from exc
                    chunk.provider = self.name
                    yield chunk
        except httpx2.TimeoutException as exc:
            raise ProviderTimeoutError(f"Provider '{self.name}' timed out") from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                f"Connection to provider '{self.name}' failed ({type(exc).__name__})"
            ) from exc
