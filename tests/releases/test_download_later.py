from __future__ import annotations

from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import downloads as downloads_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo

HASH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Download.Me.Later.1080p.mkv"


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def test_save_then_restart_then_download_links_back(settings: Settings) -> None:
    with _client(settings) as first:
        factory = first.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            title = titles_repo.create_title(session, kind="movie", title="Perfect Blue")
            title_id = title.id
        created = first.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})
        assert created.status_code == 201
        release_id = created.json()["id"]

    with _client(settings) as second:
        response = second.post("/api/downloads", json={"release_id": release_id})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["release_id"] == release_id
        assert body["engine"] == "qbittorrent"
        assert body["external_id"] == HASH
        assert body["status"] == "queued"
        assert body["title"] == "Perfect Blue"
        assert body["quality"] == "1080p"
        assert body["save_path"] == str(settings.downloads_dir.expanduser())

        again = second.post("/api/downloads", json={"release_id": release_id})
        assert again.status_code == 409
        duplicate = again.json()
        assert duplicate["id"] == body["id"]
        assert duplicate["code"] == "DOWNLOAD_ALREADY_TRACKED"
        assert "already in your downloads" in duplicate["message"]

        factory = second.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            rows = downloads_repo.list_downloads(session)
            assert len(rows) == 1
            assert rows[0].release_id == release_id
            assert releases_repo.get_release(session, release_id) is not None


class _FakeEngine:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def remove(self, external_id: str, delete_files: bool = False) -> None:
        self.removed.append(external_id)


def test_download_again_after_cancel_requeues_the_same_row(settings: Settings) -> None:
    with _client(settings) as client:
        engine = _FakeEngine()
        client.app.state.qbt_engine = engine  # type: ignore[attr-defined]
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            title = titles_repo.create_title(session, kind="movie", title="Perfect Blue")
            title_id = title.id
        created = client.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})
        release_id = created.json()["id"]

        started = client.post("/api/downloads", json={"release_id": release_id})
        assert started.status_code == 201
        download_id = started.json()["id"]

        cancelled = client.post(f"/api/downloads/{download_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        again = client.post("/api/downloads", json={"release_id": release_id})
        assert again.status_code == 202, again.text
        body = again.json()
        assert body["id"] == download_id
        assert body["status"] == "queued"
        assert body["progress"] == 0.0
        assert body["error"] is None
        assert engine.removed == [HASH, HASH]

        with factory() as session:
            rows = downloads_repo.list_downloads(session)
            assert len(rows) == 1
            assert rows[0].status == "queued"


def test_delete_then_download_creates_a_new_row(settings: Settings) -> None:
    with _client(settings) as client:
        engine = _FakeEngine()
        client.app.state.qbt_engine = engine  # type: ignore[attr-defined]
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            title = titles_repo.create_title(session, kind="movie", title="Perfect Blue")
            title_id = title.id
        created = client.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})
        release_id = created.json()["id"]

        started = client.post("/api/downloads", json={"release_id": release_id})
        assert started.status_code == 201
        download_id = started.json()["id"]
        cancelled = client.post(f"/api/downloads/{download_id}/cancel")
        assert cancelled.status_code == 200

        removed = client.delete(f"/api/downloads/{download_id}")
        assert removed.status_code == 204
        with factory() as session:
            assert downloads_repo.list_downloads(session) == []

        again = client.post("/api/downloads", json={"release_id": release_id})
        assert again.status_code == 201, again.text
        body = again.json()
        assert body["id"] != download_id
        assert body["status"] == "queued"
        with factory() as session:
            rows = downloads_repo.list_downloads(session)
            assert len(rows) == 1
            assert rows[0].id == body["id"]


def test_unsaving_does_not_touch_a_download(settings: Settings) -> None:
    with _client(settings) as client:
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            title = titles_repo.create_title(session, kind="movie", title="Dune")
            title_id = title.id
        created = client.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})
        release_id = created.json()["id"]

        started = client.post("/api/downloads", json={"release_id": release_id})
        assert started.status_code == 201

        removed = client.delete(f"/api/releases/{release_id}/save")
        assert removed.status_code == 200
        assert removed.json()["is_saved"] is False

        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            rows = downloads_repo.list_downloads(session)
            assert [row.release_id for row in rows] == [release_id]

        saved = client.get(f"/api/titles/{title_id}/releases/saved")
        assert saved.json()["releases"] == []


def test_download_unknown_release_is_404(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.post("/api/downloads", json={"release_id": "missing"})
    assert response.status_code == 404
    assert response.json()["code"] == "RELEASE_NOT_FOUND"


def test_download_without_magnet_is_422(settings: Settings) -> None:
    with _client(settings) as client:
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            title = titles_repo.create_title(session, kind="movie", title="Bare")
            row = releases_repo.create_release(session, title_id=title.id)
            release_id = row.id
        response = client.post("/api/downloads", json={"release_id": release_id})
    assert response.status_code == 422
    assert response.json()["code"] == "RELEASE_NOT_DOWNLOADABLE"
