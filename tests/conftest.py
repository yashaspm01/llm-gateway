import pytest
from fastapi.testclient import TestClient
from helpers import issue_key
from pydantic import SecretStr

from gateway.app import create_app
from gateway.config import Settings
from gateway.providers.mock import MockProvider


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite://",
        admin_token=SecretStr("admin-secret"),
        api_key_pepper=SecretStr("test-pepper"),
        auto_create_tables=True,
    )


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def client(provider, settings):
    # `with` keeps ONE event loop alive for the whole test and runs startup/shutdown.
    with TestClient(create_app(provider=provider, settings=settings)) as test_client:
        yield test_client


@pytest.fixture
def auth(client) -> dict[str, str]:
    return issue_key(client)
