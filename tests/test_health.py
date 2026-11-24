def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_request_id_is_generated(client):
    assert client.get("/health").headers["x-request-id"]


def test_request_id_is_echoed(client):
    response = client.get("/health", headers={"x-request-id": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
