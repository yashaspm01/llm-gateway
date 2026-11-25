"""Build the configured provider from settings."""

from gateway.config import Settings
from gateway.providers.base import Provider
from gateway.providers.http import OpenAICompatibleProvider
from gateway.providers.mock import MockProvider
from gateway.retry import RetryingProvider, RetryPolicy


def build_provider(settings: Settings) -> Provider:
    if settings.provider == "mock":
        return MockProvider()
    if settings.provider_base_url is None:
        raise ValueError("provider_base_url is required for provider=openai_compatible")
    api_key = settings.provider_api_key.get_secret_value() if settings.provider_api_key else None
    http = OpenAICompatibleProvider(
        name=settings.provider_name,
        base_url=settings.provider_base_url,
        api_key=api_key,
        timeout_s=settings.provider_attempt_timeout_s,
    )
    policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_s=settings.retry_base_delay_s,
        max_delay_s=settings.retry_max_delay_s,
    )
    return RetryingProvider(http, policy)
