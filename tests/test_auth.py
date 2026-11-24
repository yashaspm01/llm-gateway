import pytest
from helpers import ADMIN, BODY, URL, bearer, create_key, create_team


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Basic abc"},
        {"authorization": "Bearer"},
        {"authorization": "Bearer gw_notarealkey"},
    ],
    ids=["no-header", "wrong-scheme", "empty-token", "unknown-key"],
)
def test_bad_credentials_are_401(client, provider, headers):
    response = client.post(URL, json=BODY, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["type"] == "authentication_error"
    assert provider.calls == 0


def test_valid_key_works(client, auth):
    assert client.post(URL, json=BODY, headers=auth).status_code == 200


def test_revoked_key_stops_working(client):
    created = create_key(client, create_team(client))
    headers = bearer(created)
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
    assert client.delete(f"/admin/keys/{created['id']}", headers=ADMIN).status_code == 204
    assert client.post(URL, json=BODY, headers=headers).status_code == 401


def test_unauthenticated_callers_get_401_before_validation(client):
    response = client.post(URL, json={"model": "m"})
    assert response.status_code == 401
