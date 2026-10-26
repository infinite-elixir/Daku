from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from daku.config import Settings
from daku.repositories import downloads as downloads_repo
from daku.services.engines.base import AddRequest, EngineStatus, Health
from daku.services.queue import queue_direct

URL = "https://example.test/file.mkv"


class FakeAria2:
    name = "aria2"

    def __init__(self) -> None:
        self.healthy = True
        self.added: list[AddRequest] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.removed: list[str] = []

    async def add(self, request: AddRequest) -> str:
        self.added.append(request)
        return "gid-123"

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        return {}

    async def pause(self, external_id: str) -> None:
        self.paused.append(external_id)

    async def resume(self, external_id: str) -> None:
        self.resumed.append(external_id)

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None:
        self.removed.append(external_id)

    async def health(self) -> Health:
        return Health(ok=self.healthy, detail="aria2 1.36" if self.healthy else "refused")


class FakeSupervisor:
    def __init__(self, *, available: bool = True, started: bool = True) -> None:
        self.available = available
        self.started = started
        self.calls = 0

    async def ensure_started(self) -> bool:
        self.calls += 1
        return self.started


def _install(client: TestClient, engine: FakeAria2, supervisor: FakeSupervisor) -> None:
    client.app.state.aria2_engine = engine  # type: ignore[attr-defined]
    client.app.state.aria2_supervisor = supervisor  # type: ignore[attr-defined]


@respx.mock
def test_direct_url_creates_an_aria2_row(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    engine = FakeAria2()
    supervisor = FakeSupervisor()
    _install(client, engine, supervisor)
    respx.head(URL).mock(return_value=httpx.Response(200, headers={"content-length": "2048"}))

    response = client.post("/api/downloads", json={"url": URL, "filename": "file.mkv"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["engine"] == "aria2"
    assert body["external_id"] == "gid-123"
    assert body["status"] == "active"
    assert body["source"] == "direct"
    assert body["total_bytes"] == 2048
    assert body["save_path"] == str(settings.downloads_dir.expanduser())
    assert len(engine.added) == 1
    assert engine.added[0].url == URL
    assert engine.added[0].filename == "file.mkv"
    assert engine.added[0].save_path == str(settings.downloads_dir.expanduser())

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = downloads_repo.list_downloads(session)
        assert len(rows) == 1
        assert rows[0].url == URL


def test_magnet_url_is_rejected_with_a_clear_code(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeAria2(), FakeSupervisor())

    response = client.post("/api/downloads", json={"url": "magnet:?xt=urn:btih:" + "a" * 40})

    assert response.status_code == 422
    assert response.json()["code"] == "ARIA2_TORRENT_FORBIDDEN"


def test_torrent_file_url_is_rejected(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client
    _install(client, FakeAria2(), FakeSupervisor())

    response = client.post("/api/downloads", json={"url": "https://example.test/x.torrent"})

    assert response.status_code == 422
    assert response.json()["code"] == "ARIA2_TORRENT_FORBIDDEN"


def test_both_sources_is_a_validation_error(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client

    response = client.post("/api/downloads", json={"release_id": "x", "url": URL})

    assert response.status_code == 422


@respx.mock
def test_space_preflight_refuses_a_download(
    app_client: tuple[TestClient, Settings], monkeypatch: Any
) -> None:
    client, _settings = app_client
    engine = FakeAria2()
    _install(client, engine, FakeSupervisor())
    respx.head(URL).mock(
        return_value=httpx.Response(200, headers={"content-length": str(10 * 1024**3)})
    )
    monkeypatch.setattr("daku.services.queue.free_bytes", lambda _path: 1024)

    response = client.post("/api/downloads", json={"url": URL})

    assert response.status_code == 507
    assert response.json()["code"] == "INSUFFICIENT_DISK_SPACE"
    assert engine.added == []


def test_missing_aria2_does_not_break_torrents(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    engine = FakeAria2()
    engine.healthy = False
    _install(client, engine, FakeSupervisor(available=False, started=False))

    response = client.post("/api/downloads", json={"url": URL})

    assert response.status_code == 503
    assert response.json()["code"] == "ARIA2_UNAVAILABLE"


def test_pause_resume_cancel_and_retry_are_idempotent(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    engine = FakeAria2()
    _install(client, engine, FakeSupervisor())
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = queue_direct(session, url=URL, gid="gid-123", save_path="/downloads")
        download_id = row.id

    paused = client.post(f"/api/downloads/{download_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert engine.paused == ["gid-123"]

    again = client.post(f"/api/downloads/{download_id}/pause")
    assert again.status_code == 200
    assert again.json()["status"] == "paused"
    assert engine.paused == ["gid-123"]

    resumed = client.post(f"/api/downloads/{download_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    assert engine.resumed == ["gid-123"]

    cancelled = client.post(f"/api/downloads/{download_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert engine.removed == ["gid-123"]

    cancelled_again = client.post(f"/api/downloads/{download_id}/cancel")
    assert cancelled_again.status_code == 200
    assert cancelled_again.json()["status"] == "cancelled"

    retried = client.post(f"/api/downloads/{download_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert retried.json()["error"] is None


def test_unknown_download_is_404(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client

    response = client.post("/api/downloads/missing/pause")

    assert response.status_code == 404
    assert response.json()["code"] == "DOWNLOAD_NOT_FOUND"
