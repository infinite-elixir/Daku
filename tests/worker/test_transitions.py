from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from daku.config import Settings
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import events as events_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.engines.base import AddRequest, EngineError, EngineStatus, Health
from daku.services.queue import queue_release
from daku.worker import Worker

HASH = "d" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Movie.1080p.mkv"


class FakeEngine:
    name = "qbittorrent"

    def __init__(self) -> None:
        self.added: list[AddRequest] = []
        self.statuses: dict[str, EngineStatus] = {}
        self.poll_error: EngineError | None = None
        self.add_error: EngineError | None = None

    async def add(self, request: AddRequest) -> str:
        if self.add_error is not None:
            raise self.add_error
        self.added.append(request)
        return request.external_id

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        if self.poll_error is not None:
            raise self.poll_error
        return {key: value for key, value in self.statuses.items() if key in external_ids}

    async def pause(self, external_id: str) -> None: ...

    async def resume(self, external_id: str) -> None: ...

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None: ...

    async def health(self) -> Health:
        return Health(ok=True, detail="fake")


def _queued_download(factory: Any) -> str:
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="Dune")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=HASH,
            magnet=MAGNET,
            filename="Movie.1080p.mkv",
            size_bytes=1000,
        )
        return queue_release(session, release, save_path="/downloads").download.id


def _download(factory: Any, download_id: str) -> Download:
    with factory() as session:
        row = downloads_repo.get_download(session, download_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_queued_download_is_added_and_becomes_active(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    engine = FakeEngine()
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "active"
    assert row.external_id == HASH
    assert row.error is None
    assert len(engine.added) == 1
    assert engine.added[0].url == MAGNET
    with factory() as session:
        release = releases_repo.get_release(session, row.release_id or "")
        assert release is not None
        assert engine.added[0].tag == release.title_id


async def test_queued_download_is_adopted_instead_of_added_twice(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH, status="active", progress=0.5, downloaded_bytes=500, total_bytes=1000
    )
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "active"
    assert row.progress == 0.5
    assert engine.added == []


async def test_active_download_reaches_complete(db: tuple[Any, Any], settings: Settings) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    with factory() as session:
        downloads_repo.update_download(session, download_id, status="active")
    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH,
        status="complete",
        progress=1.0,
        downloaded_bytes=1000,
        total_bytes=1000,
        speed_bps=0,
        eta_seconds=0,
    )
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "complete"
    assert row.progress == 1.0
    assert row.downloaded_bytes == 1000
    with factory() as session:
        events = events_repo.list_events(session)
        assert [event.kind for event in events] == ["download.status"]
        assert events[0].payload()["status"] == "complete"


async def test_engine_error_state_fails_the_row(db: tuple[Any, Any], settings: Settings) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    with factory() as session:
        downloads_repo.update_download(session, download_id, status="active")
    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(external_id=HASH, status="failed", error="missing files")
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "failed"
    assert row.error == "missing files"


async def test_unreachable_engine_keeps_active_with_last_error(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    with factory() as session:
        downloads_repo.update_download(session, download_id, status="active")
    engine = FakeEngine()
    engine.poll_error = EngineError("QBT_UNREACHABLE", "qBittorrent is unreachable: refused")
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "active"
    assert "unreachable" in (row.error or "")


async def test_transient_add_error_keeps_the_row_queued(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    engine = FakeEngine()
    engine.add_error = EngineError("QBT_UNREACHABLE", "qBittorrent is unreachable")
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "queued"
    assert "unreachable" in (row.error or "")


async def test_rejected_add_fails_the_row(db: tuple[Any, Any], settings: Settings) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    engine = FakeEngine()
    engine.add_error = EngineError("QBT_REJECTED", "qBittorrent rejected the torrent")
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "failed"
    assert row.error == "qBittorrent rejected the torrent"


async def test_vanished_torrent_fails_the_row(db: tuple[Any, Any], settings: Settings) -> None:
    _engine, factory = db
    download_id = _queued_download(factory)
    with factory() as session:
        downloads_repo.update_download(session, download_id, status="active")
    engine = FakeEngine()
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "failed"
    assert "no longer known" in (row.error or "")


class AriaFake(FakeEngine):
    name = "aria2"

    def __init__(self) -> None:
        super().__init__()
        self.adopted: list[str] = []

    async def adopt(self, url: str) -> str | None:
        self.adopted.append(url)
        return "new-gid" if url == "https://example.test/file.mkv" else None


async def test_missing_aria2_gid_is_readopted_after_a_restart(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    with factory() as session:
        row = downloads_repo.create_download(
            session,
            source="direct",
            engine="aria2",
            external_id="old-gid",
            status="active",
            save_path="/downloads",
            url="https://example.test/file.mkv",
        )
        download_id = row.id

    engine = AriaFake()
    engine.statuses["old-gid"] = EngineStatus(
        external_id="old-gid",
        status="failed",
        error="download no longer exists in aria2",
    )
    worker = Worker(factory, settings, {"aria2": engine})

    await worker.tick()

    row = _download(factory, download_id)
    assert engine.adopted == ["https://example.test/file.mkv"]
    assert row.external_id == "new-gid"
    assert row.status == "active"
    assert row.error == "re-adopted after aria2 restart"

    engine.statuses["new-gid"] = EngineStatus(
        external_id="new-gid", status="active", progress=0.4, downloaded_bytes=400, total_bytes=1000
    )
    await worker.tick()

    row = _download(factory, download_id)
    assert row.status == "active"
    assert row.progress == 0.4
    assert row.error is None
