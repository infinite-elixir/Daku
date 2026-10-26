from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.errors import AppError
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.organize.ffprobe import MediaInfo

MB = 1024 * 1024


def _make(path: Path, size: int = 60 * MB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def _download(factory: sessionmaker[Session], root: Path) -> Download:
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="Dune")
        release = releases_repo.create_release(
            session, title_id=title.id, info_hash="b" * 40, filename="Dune.mkv"
        )
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id="b" * 40,
            release_id=release.id,
            status="complete",
            save_path=str(root),
        )
        session.expunge(row)
        return row


async def _probe(_path: Path, **_kwargs: Any) -> MediaInfo:
    return MediaInfo(container="matroska", vcodec="hevc", acodec="dts", duration_s=100.0)


def test_inspect_endpoint_persists_media_files(
    app_client: tuple[TestClient, Settings],
    monkeypatch: Any,
) -> None:
    client, settings = app_client
    root = settings.downloads_dir
    _make(root / "Dune.2021.mkv", 100 * MB)
    _make(root / "Dune.2021.srt", 10)
    monkeypatch.setattr("daku.services.organize.select.probe", _probe)

    download = _download(client.app.state.session_factory, root)  # type: ignore[attr-defined]
    response = client.post(f"/api/downloads/{download.id}/inspect")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["primary_id"] is not None
    assert len(payload["media_files"]) == 1
    primary = payload["media_files"][0]
    assert primary["vcodec"] == "hevc"
    assert primary["is_primary"] is True
    assert primary["episode_id"] is None
    assert primary["sidecars"] == [str(root / "Dune.2021.srt")]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = media_repo.list_by_download(session, download.id)
    assert len(rows) == 1


def test_inspect_endpoint_reports_no_video_files(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    root = settings.downloads_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "readme.txt").write_text("nothing here")

    download = _download(client.app.state.session_factory, root)  # type: ignore[attr-defined]
    response = client.post(f"/api/downloads/{download.id}/inspect")

    assert response.status_code == 422
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


def test_missing_ffprobe_returns_a_readable_error(
    app_client: tuple[TestClient, Settings],
    monkeypatch: Any,
) -> None:
    client, settings = app_client
    root = settings.downloads_dir
    _make(root / "Dune.mkv", 100 * MB)

    async def missing(_path: Path, **_kwargs: Any) -> MediaInfo:
        raise AppError(
            code="FFPROBE_MISSING",
            message="ffprobe is not installed; install ffmpeg",
            status_code=503,
        )

    monkeypatch.setattr("daku.services.organize.select.probe", missing)
    download = _download(client.app.state.session_factory, root)  # type: ignore[attr-defined]
    response = client.post(f"/api/downloads/{download.id}/inspect")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "MEDIA_INSPECTION_FAILED"
    assert "ffmpeg" in body["message"]


def test_inspect_unknown_download_is_404(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client
    response = client.post("/api/downloads/nope/inspect")

    assert response.status_code == 404
    assert response.json()["code"] == "DOWNLOAD_NOT_FOUND"
