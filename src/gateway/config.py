"""Configuration from environment variables (12-factor style)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PEPPER = "dev-only-pepper-change-me"

_DROPPED_QUERY_PARAMS = {"channel_binding", "pgbouncer"}  # asyncpg rejects these


def normalize_database_url(url: str) -> str:
    """Make a provider-supplied Postgres URL work with SQLAlchemy's asyncpg driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+asyncpg://"):
        return url
    parts = urlsplit(url)
    query = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _DROPPED_QUERY_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Settings(BaseSettings):
    """Every setting can be overridden with a GATEWAY_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    app_name: str = "llm-gateway"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    provider_timeout_s: float = Field(default=30.0, gt=0)  # total deadline, retries included

    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    admin_token: SecretStr | None = None  # None disables the admin API entirely
    api_key_pepper: SecretStr = SecretStr(DEV_PEPPER)
    auto_create_tables: bool = False  # True only in tests; real databases use migrations

    # The upstream provider
    provider: Literal["mock", "openai_compatible"] = "mock"
    provider_name: str = "upstream"
    provider_base_url: str | None = None
    provider_api_key: SecretStr | None = None
    provider_attempt_timeout_s: float = Field(default=10.0, gt=0)  # one attempt, not the total

    # Multiple providers and the circuit breaker
    providers_file: Path | None = None  # JSON file; takes precedence over the single provider
    breaker_failure_threshold: int = Field(default=5, ge=1)
    breaker_recovery_timeout_s: float = Field(default=30.0, ge=0)

    # Retries
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_base_delay_s: float = Field(default=0.2, ge=0)
    retry_max_delay_s: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def _prod_needs_a_real_pepper(self) -> Self:
        if self.environment == "prod" and self.api_key_pepper.get_secret_value() == DEV_PEPPER:
            raise ValueError("GATEWAY_API_KEY_PEPPER must be set explicitly in prod")
        return self

    @model_validator(mode="after")
    def _http_provider_needs_a_url(self) -> Self:
        if (
            self.provider == "openai_compatible"
            and not self.provider_base_url
            and self.providers_file is None
        ):
            raise ValueError("GATEWAY_PROVIDER_BASE_URL is required for openai_compatible")
        return self

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
