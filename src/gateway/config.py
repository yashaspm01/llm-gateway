"""Configuration from environment variables (12-factor style)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PEPPER = "dev-only-pepper-change-me"


class Settings(BaseSettings):
    """Every setting can be overridden with a GATEWAY_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    app_name: str = "llm-gateway"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    provider_timeout_s: float = Field(default=30.0, gt=0)

    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    admin_token: SecretStr | None = None  # None disables the admin API entirely
    api_key_pepper: SecretStr = SecretStr(DEV_PEPPER)
    auto_create_tables: bool = False  # True only in tests; real databases use migrations

    @model_validator(mode="after")
    def _prod_needs_a_real_pepper(self) -> Self:
        if self.environment == "prod" and self.api_key_pepper.get_secret_value() == DEV_PEPPER:
            raise ValueError("GATEWAY_API_KEY_PEPPER must be set explicitly in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
