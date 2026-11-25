import pytest
from helpers import ADMIN, BODY, URL, bearer, create_key, create_team, issue_key


def test_new_team_defaults(client):
    body = client.post("/admin/teams", json={"name": "d"}, headers=ADMIN).json()
    assert body["rate_limit_per_minute"] == 60
    assert body["daily_token_budget"] is None


def test_rate_limit_returns_429_with_retry_after(client):
    headers = issue_key(client, rate_limit_per_minute=2)
    codes = [client.post(URL, json=BODY, headers=headers).status_code for _ in range(2)]
    assert codes == [200, 200]
    response = client.post(URL, json=BODY, headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_exceeded"
    assert 1 <= int(response.headers["retry-after"]) <= 30


def test_rate_limited_requests_never_reach_the_provider(client, provider):
    headers = issue_key(client, rate_limit_per_minute=1)
    for _ in range(3):
        client.post(URL, json=BODY, headers=headers)
    assert provider.calls == 1


def test_limits_are_per_team(client):
    slow = issue_key(client, "slow", rate_limit_per_minute=1)
    other = issue_key(client, "other")
    assert client.post(URL, json=BODY, headers=slow).status_code == 200
    assert client.post(URL, json=BODY, headers=slow).status_code == 429
    assert client.post(URL, json=BODY, headers=other).status_code == 200


def test_budget_blocks_once_it_is_used_up(client):
    headers = issue_key(client, daily_token_budget=10)  # one mock call costs 7 tokens
    codes = [client.post(URL, json=BODY, headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]  # the budget is checked before a call, so it can overshoot
    response = client.post(URL, json=BODY, headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert 1 <= int(response.headers["retry-after"]) <= 86_400


def test_no_budget_means_unlimited(client):
    headers = issue_key(client)
    codes = [client.post(URL, json=BODY, headers=headers).status_code for _ in range(5)]
    assert codes == [200] * 5


def test_raising_the_budget_unblocks_the_team(client):
    team_id = create_team(client, daily_token_budget=5)
    headers = bearer(create_key(client, team_id))
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
    assert client.post(URL, json=BODY, headers=headers).status_code == 429
    patched = client.patch(
        f"/admin/teams/{team_id}", json={"daily_token_budget": None}, headers=ADMIN
    )
    assert patched.status_code == 200
    assert patched.json()["daily_token_budget"] is None
    assert client.post(URL, json=BODY, headers=headers).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [{"rate_limit_per_minute": None}, {"rate_limit_per_minute": 0}],
    ids=["null-rate", "zero-rate"],
)
def test_patch_rejects_bad_limits(client, changes):
    team_id = create_team(client)
    response = client.patch(f"/admin/teams/{team_id}", json=changes, headers=ADMIN)
    assert response.status_code == 400


def test_patch_unknown_team_is_404(client):
    response = client.patch("/admin/teams/999", json={"daily_token_budget": 5}, headers=ADMIN)
    assert response.status_code == 404


def test_raising_the_rate_limit_applies_immediately(client):
    team_id = create_team(client, rate_limit_per_minute=1)
    headers = bearer(create_key(client, team_id))
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
    assert client.post(URL, json=BODY, headers=headers).status_code == 429
    client.patch(f"/admin/teams/{team_id}", json={"rate_limit_per_minute": 100}, headers=ADMIN)
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
