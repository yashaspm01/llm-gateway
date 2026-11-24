"""The gateway's own error types. Each one knows its HTTP status and error type."""


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


class ProviderTimeoutError(ProviderError):
    status_code = 504
    error_type = "provider_timeout"
