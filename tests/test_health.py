from __future__ import annotations

from fastapi.testclient import TestClient

from daku.config import Settings


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"status", "version", "uptime_s", "data_dir"}
    assert payload["status"] == "ok"
    assert isinstance(payload["uptime_s"], (int, float))


def test_doctor_lists_every_integration(client: TestClient) -> None:
    response = client.get("/api/system/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert "checked_at" in payload
    names = {check["name"] for check in payload["checks"]}
    assert names == {
        "qbittorrent",
        "aria2",
        "jellyfin",
        "telegram",
        "torrentio",
        "nyaa",
        "tvdb",
        "metadata_sources",
        "omdb",
        "ffprobe",
        "disk",
        "mapping",
        "magnet_probe",
    }
    for check in payload["checks"]:
        assert check["status"]
        assert check["detail"]


def test_startup_creates_dirs_and_secrets(app_client: tuple[TestClient, Settings]) -> None:
    client, settings = app_client
    assert client.get("/api/health").status_code == 200
    assert settings.data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert settings.secrets_file.exists()
