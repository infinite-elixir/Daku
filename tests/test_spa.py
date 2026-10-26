from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_returns_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Daku" in response.text


def test_client_route_returns_index(client: TestClient) -> None:
    response = client.get("/library/some-title")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_unknown_api_route_returns_json_404(client: TestClient) -> None:
    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_api_route_is_not_shadowed_by_spa(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
