import pytest
from pydantic import SecretStr

from gateway.config import Settings


def test_prod_requires_a_real_pepper():
    with pytest.raises(ValueError):
        Settings(environment="prod")


def test_prod_with_a_real_pepper_is_accepted():
    settings = Settings(environment="prod", api_key_pepper=SecretStr("x" * 32))
    assert settings.environment == "prod"


def test_http_provider_requires_a_base_url():
    with pytest.raises(ValueError):
        Settings(provider="openai_compatible", provider_base_url=None)
