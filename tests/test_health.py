from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from daku.config import Settings
from daku.repositories import media_files as media_files_repo
from daku.repositories import titles as titles_repo


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
        "animetosho",
        "torrents-csv",
        "tvdb",
        "metadata_sources",
        "omdb",
        "ffprobe",
        "ffmpeg",
        "mpv",
        "disk",
        "mapping",
        "magnet_probe",
        "library",
    }
    for check in payload["checks"]:
        assert check["status"]
        assert check["detail"]


def test_doctor_warns_for_media_rows_without_files(client: TestClient, tmp_path: Path) -> None:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        title = titles_repo.create_title(session, kind="movie", title="Gone")
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            src_path=str(tmp_path / "gone.mkv"),
            is_primary=True,
        )

    response = client.get("/api/system/doctor")

    assert response.status_code == 200
    library = next(check for check in response.json()["checks"] if check["name"] == "library")
    assert library["status"] == "warn"
    assert "1 media file(s) missing" in library["detail"]


def test_startup_creates_dirs_and_secrets(app_client: tuple[TestClient, Settings]) -> None:
    client, settings = app_client
    assert client.get("/api/health").status_code == 200
    assert settings.data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert settings.secrets_file.exists()
