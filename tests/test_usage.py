from fastapi.testclient import TestClient
from helpers import ADMIN, BODY, URL, bearer, create_key, create_team

from gateway.app import create_app
from gateway.errors import ProviderError
from gateway.providers.mock import MockProvider


def test_usage_is_recorded_per_request(client):
    team_id = create_team(client)
    headers = bearer(create_key(client, team_id))
    first = client.post(URL, json=BODY, headers=headers).json()["usage"]
    second = client.post(URL, json=BODY, headers=headers).json()["usage"]

    usage = client.get(f"/admin/teams/{team_id}/usage", headers=ADMIN).json()
    assert usage["requests"] == 2
    assert usage["prompt_tokens"] == first["prompt_tokens"] + second["prompt_tokens"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_usage_is_isolated_between_teams(client):
    busy = create_team(client, "busy")
    quiet = create_team(client, "quiet")
    headers = bearer(create_key(client, busy))
    client.post(URL, json=BODY, headers=headers)

    assert client.get(f"/admin/teams/{busy}/usage", headers=ADMIN).json()["requests"] == 1
    assert client.get(f"/admin/teams/{quiet}/usage", headers=ADMIN).json()["requests"] == 0


def test_failed_calls_are_not_recorded(settings):
    app = create_app(provider=MockProvider(failure=ProviderError("down")), settings=settings)
    with TestClient(app) as client:
        team_id = create_team(client)
        headers = bearer(create_key(client, team_id))
        assert client.post(URL, json=BODY, headers=headers).status_code == 502
        usage = client.get(f"/admin/teams/{team_id}/usage", headers=ADMIN).json()
    assert usage["requests"] == 0


def test_usage_for_unknown_team_is_404(client):
    assert client.get("/admin/teams/999/usage", headers=ADMIN).status_code == 404
