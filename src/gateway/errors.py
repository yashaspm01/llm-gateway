"""The gateway's own error types. Each one knows its HTTP status and error type."""

import math


class GatewayError(Exception):
    status_code = 500
    error_type = "internal_error"
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidRequestError(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"


class AuthenticationError(GatewayError):
    status_code = 401
    error_type = "authentication_error"
    headers = {"WWW-Authenticate": "Bearer"}


class NotFoundError(GatewayError):
    status_code = 404
    error_type = "not_found_error"


class ConflictError(GatewayError):
    status_code = 409
    error_type = "conflict_error"


class ProviderError(GatewayError):
    """The upstream provider failed or returned something unusable."""

    status_code = 502
    error_type = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable  # would trying again plausibly help?
        self.retry_after_s = retry_after_s  # upstream's own "come back in N seconds"


class ProviderTimeoutError(ProviderError):
    status_code = 504
    error_type = "provider_timeout"


class RateLimitedError(GatewayError):
    """Caller exceeded a limit. Carries how long they should wait."""

    status_code = 429
    error_type = "rate_limit_exceeded"

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.headers = {"Retry-After": str(max(1, math.ceil(retry_after_s)))}


class BudgetExceededError(RateLimitedError):
    error_type = "budget_exceeded"
