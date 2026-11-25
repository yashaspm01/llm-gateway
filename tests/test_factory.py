import json

import pytest
from pydantic import SecretStr

from gateway.failover import FailoverProvider
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


def write_providers(tmp_path, providers):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": providers}))
    return path


def test_providers_file_builds_a_failover_chain(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIMARY_KEY", "k1")
    path = write_providers(
        tmp_path,
        [
            {"name": "primary", "base_url": "https://a.example/v1", "api_key_env": "PRIMARY_KEY"},
            {"name": "backup", "base_url": "https://b.example/v1"},
        ],
    )
    provider = build_provider(settings.model_copy(update={"providers_file": path}))
    assert isinstance(provider, FailoverProvider)
    assert [row["name"] for row in provider.status()] == ["primary", "backup"]


def test_a_single_provider_in_the_file_is_not_wrapped_in_failover(settings, tmp_path):
    path = write_providers(tmp_path, [{"name": "only", "base_url": "https://a.example/v1"}])
    provider = build_provider(settings.model_copy(update={"providers_file": path}))
    assert isinstance(provider, RetryingProvider)
    assert provider.name == "only"


def test_a_missing_api_key_variable_fails_fast(settings, tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    path = write_providers(
        tmp_path,
        [{"name": "p", "base_url": "https://a.example/v1", "api_key_env": "MISSING_KEY"}],
    )
    with pytest.raises(ValueError, match="MISSING_KEY"):
        build_provider(settings.model_copy(update={"providers_file": path}))
