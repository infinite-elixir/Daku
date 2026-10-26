from __future__ import annotations

from fastapi.testclient import TestClient


def test_settings_exposes_only_release_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"release", "sources"}
    assert payload["release"]["providers"]["movie"]
    assert "qbt_pass" not in response.text
    assert "config_dir" not in response.text


def test_settings_patch_is_persisted_and_validated(client: TestClient) -> None:
    response = client.patch(
        "/api/settings",
        json={"release": {"providers": {"anime": [" Nyaa ", "nyaa"]}, "limit": 50}},
    )
    assert response.status_code == 200
    assert response.json()["release"]["providers"]["anime"] == ["nyaa"]
    assert response.json()["release"]["limit"] == 50
    assert client.get("/api/settings").json()["release"]["providers"]["anime"] == ["nyaa"]

    invalid = client.patch("/api/settings", json={"release": {"providers": {"movie": []}}})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_SETTING"
    unknown = client.patch("/api/settings", json={"release": {"wat": 1}})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "UNKNOWN_SETTING"
