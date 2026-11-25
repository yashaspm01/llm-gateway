"""Build the configured provider stack from settings.

Each provider becomes  Retry( CircuitBreaker( Http ) ),  and several are combined by Failover.
"""

import os

from gateway.circuit import CircuitBreaker, CircuitBreakerProvider
from gateway.config import Settings
from gateway.failover import FailoverProvider
from gateway.providers.base import Provider
from gateway.providers.config import ProviderConfig, load_provider_configs
from gateway.providers.http import OpenAICompatibleProvider
from gateway.providers.mock import MockProvider
from gateway.retry import RetryingProvider, RetryPolicy


def _key_from_env(config: ProviderConfig) -> str | None:
    if config.api_key_env is None:
        return None
    value = os.environ.get(config.api_key_env)
    if not value:
        raise ValueError(
            f"Environment variable {config.api_key_env} (API key for '{config.name}') is not set"
        )
    return value


def _build_stack(settings: Settings, config: ProviderConfig, api_key: str | None) -> Provider:
    http = OpenAICompatibleProvider(
        name=config.name,
        base_url=config.base_url,
        api_key=api_key,
        timeout_s=settings.provider_attempt_timeout_s,
        model=config.model,
    )
    breaker = CircuitBreaker(
        config.name,
        failure_threshold=settings.breaker_failure_threshold,
        recovery_timeout_s=settings.breaker_recovery_timeout_s,
    )
    policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_s=settings.retry_base_delay_s,
        max_delay_s=settings.retry_max_delay_s,
    )
    return RetryingProvider(CircuitBreakerProvider(http, breaker), policy)


def build_provider(settings: Settings) -> Provider:
    stacks: list[Provider]
    if settings.providers_file is not None:
        configs = load_provider_configs(settings.providers_file)
        stacks = [_build_stack(settings, config, _key_from_env(config)) for config in configs]
    elif settings.provider == "mock":
        return MockProvider()
    else:
        if settings.provider_base_url is None:
            raise ValueError("provider_base_url is required for provider=openai_compatible")
        config = ProviderConfig(name=settings.provider_name, base_url=settings.provider_base_url)
        key = settings.provider_api_key.get_secret_value() if settings.provider_api_key else None
        stacks = [_build_stack(settings, config, key)]
    return stacks[0] if len(stacks) == 1 else FailoverProvider(stacks)
