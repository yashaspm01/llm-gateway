from pydantic import SecretStr

from gateway.providers.factory import build_provider
from gateway.providers.mock import MockProvider
from gateway.retry import RetryingProvider


def test_mock_is_the_default(settings):
    assert isinstance(build_provider(settings), MockProvider)


def test_http_provider_is_wrapped_in_retries(settings):
    configured = settings.model_copy(
        update={
            "provider": "openai_compatible",
            "provider_base_url": "https://upstream.example/v1",
            "provider_api_key": SecretStr("k"),
        }
    )
    provider = build_provider(configured)
    assert isinstance(provider, RetryingProvider)
    assert provider.name == "upstream"
