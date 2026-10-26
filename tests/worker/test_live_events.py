from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from daku.config import Settings
from daku.events import EventBus
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.engines.base import AddRequest, EngineStatus, Health
from daku.services.organize.ffprobe import MediaInfo
from daku.services.queue import queue_release
from daku.worker import Worker

HASH = "c" * 40
MB = 1024 * 1024


class FakeEngine:
    name = "qbittorrent"

    def __init__(self) -> None:
        self.statuses: dict[str, EngineStatus] = {}

    async def add(self, request: AddRequest) -> str:
        return request.external_id

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        return {key: value for key, value in self.statuses.items() if key in external_ids}

    async def pause(self, external_id: str) -> None: ...

    async def resume(self, external_id: str) -> None: ...

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None: ...

    async def health(self) -> Health:
        return Health(ok=True, detail="fake")


def _queued(factory: Any, save_path: Path) -> str:
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="Dune")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=HASH,
            magnet=f"magnet:?xt=urn:btih:{HASH}",
            filename="Dune.mkv",
        )
        return queue_release(session, release, save_path=save_path).download.id


def _row(factory: Any, download_id: str) -> Download:
    with factory() as session:
        row = downloads_repo.get_download(session, download_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_worker_publishes_safe_progress_events(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    bus = EventBus()
    queue = bus.subscribe()
    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH, status="active", progress=0.5, downloaded_bytes=50, total_bytes=100
    )
    worker = Worker(factory, settings, {"qbittorrent": engine}, bus=bus, auto_inspect=False)
    _queued(factory, settings.downloads_dir)

    await worker.tick()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert events
    assert all(event.type == "download.updated" for event in events)
    assert events[-1].data["progress"] == 0.5
    assert "save_path" not in events[-1].data
    assert "magnet" not in events[-1].data


async def test_progress_events_are_throttled_to_one_per_second(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    bus = EventBus()
    queue = bus.subscribe()
    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH, status="active", progress=0.4, downloaded_bytes=40, total_bytes=100
    )
    worker = Worker(factory, settings, {"qbittorrent": engine}, bus=bus, auto_inspect=False)
    _queued(factory, settings.downloads_dir)

    await worker.tick()  # queued -> active (state change, always published)
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH, status="active", progress=0.6, downloaded_bytes=60, total_bytes=100
    )
    await worker.tick()  # progress-only, within one second of the first event

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 1
    assert events[0].data["progress"] == 0.4


async def test_completion_inspects_the_download(
    db: tuple[Any, Any], settings: Settings, monkeypatch: Any, tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    root.mkdir()
    with (root / "Dune.mkv").open("wb") as handle:
        handle.truncate(100 * MB)
    download_id = _queued(factory, root)
    with factory() as session:
        downloads_repo.update_download(session, download_id, status="active")

    async def fake_probe(_path: Path, **_kwargs: Any) -> MediaInfo:
        return MediaInfo(container="matroska", vcodec="hevc", acodec="dts")

    monkeypatch.setattr("daku.services.organize.select.probe", fake_probe)

    engine = FakeEngine()
    engine.statuses[HASH] = EngineStatus(
        external_id=HASH,
        status="complete",
        progress=1.0,
        downloaded_bytes=100,
        total_bytes=100,
    )
    worker = Worker(factory, settings, {"qbittorrent": engine})

    await worker.tick()
    await asyncio.sleep(0)

    with factory() as session:
        files = media_repo.list_by_download(session, download_id)
    assert len(files) == 1
    assert files[0].is_primary is True
    assert files[0].vcodec == "hevc"
    assert _row(factory, download_id).status == "complete"
