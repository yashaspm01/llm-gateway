import pytest
from pydantic import SecretStr

from gateway.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql://u:p@h/db?sslmode=require", "postgresql+asyncpg://u:p@h/db?ssl=require"),
        (
            "postgresql://u:p%40x@h/db?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p%40x@h/db?ssl=require",
        ),
        (
            "postgresql+asyncpg://u:p@h/db?pgbouncer=true&ssl=require",
            "postgresql+asyncpg://u:p@h/db?ssl=require",
        ),
    ],
    ids=["sqlite", "postgres-scheme", "sslmode", "neon-extras-and-encoded-password", "pgbouncer"],
)
def test_normalize_database_url(raw, expected):
    assert normalize_database_url(raw) == expected


def test_settings_normalize_the_database_url():
    settings = Settings(database_url="postgres://u:p@h/db?sslmode=require")
    assert settings.database_url == "postgresql+asyncpg://u:p@h/db?ssl=require"


def test_prod_requires_a_real_pepper():
    with pytest.raises(ValueError):
        Settings(environment="prod")


def test_prod_with_a_real_pepper_is_accepted():
    settings = Settings(environment="prod", api_key_pepper=SecretStr("x" * 32))
    assert settings.environment == "prod"


def test_http_provider_requires_a_base_url():
    with pytest.raises(ValueError):
        Settings(provider="openai_compatible", provider_base_url=None, providers_file=None)
