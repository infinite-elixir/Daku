from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from daku.config import Settings
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.engines.base import AddRequest, EngineError, EngineStatus, Health
from daku.services.queue import queue_release
from daku.worker import Worker

HASH = "e" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Show.S02.1080p.mkv"


class SelectionEngine:
    name = "qbittorrent"

    def __init__(self) -> None:
        self.added: list[AddRequest] = []
        self.stopped: list[str] = []
        self.selections: list[tuple[str, list[int], list[str] | None]] = []
        self.selection_error: EngineError | None = None
        self.effective: list[int] = [4]

    async def add(self, request: AddRequest) -> str:
        self.added.append(request)
        return request.external_id

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        return {}

    async def stop(self, external_id: str) -> None:
        self.stopped.append(external_id)

    async def start(self, external_id: str) -> None: ...

    async def pause(self, external_id: str) -> None:
        await self.stop(external_id)

    async def resume(self, external_id: str) -> None: ...

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None: ...

    async def set_file_selection(
        self,
        external_id: str,
        indices: Sequence[int],
        *,
        paths: Sequence[str] | None = None,
        start: bool = True,
    ) -> list[int]:
        if self.selection_error is not None:
            raise self.selection_error
        self.selections.append((external_id, list(indices), list(paths) if paths else None))
        return self.effective

    async def health(self) -> Health:
        return Health(ok=True, detail="fake")


def _queued(factory: Any) -> str:
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Show", imdb_id="tt1234567")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=HASH,
            magnet=MAGNET,
            filename="Show.S02.1080p.mkv",
        )
        return queue_release(
            session,
            release,
            save_path="/downloads",
            selected_files_json="[4]",
            file_match_json=json.dumps(
                {
                    "reason": "S02E05 exact",
                    "file_paths": ["Show.S02E05.1080p.mkv"],
                    "total_files": 12,
                }
            ),
        ).download.id


def _row(factory: Any, download_id: str) -> Download:
    with factory() as session:
        row = downloads_repo.get_download(session, download_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_a_queued_selection_is_stopped_prioritised_and_only_then_active(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued(factory)
    engine = SelectionEngine()
    worker = Worker(factory, settings, {"qbittorrent": engine}, auto_inspect=False)

    await worker.tick()

    row = _row(factory, download_id)
    assert row.status == "active"
    assert row.error is None
    assert row.selected_files_json == "[4]"
    assert engine.added[0].paused is True
    assert engine.stopped == [HASH]
    assert engine.selections == [(HASH, [4], ["Show.S02E05.1080p.mkv"])]


async def test_a_transient_selection_error_keeps_the_row_paused(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued(factory)
    engine = SelectionEngine()
    engine.selection_error = EngineError(
        "QBT_FILE_SELECTION_PENDING", "qBittorrent did not return the file list in time"
    )
    worker = Worker(factory, settings, {"qbittorrent": engine}, auto_inspect=False)

    await worker.tick()

    row = _row(factory, download_id)
    assert row.status == "paused"
    assert "FILE_SELECTION_PENDING" in (row.error or "")


async def test_a_rejected_selection_fails_with_the_stable_code(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued(factory)
    engine = SelectionEngine()
    engine.selection_error = EngineError(
        "QBT_FILE_SELECTION_FAILED", "qBittorrent did not apply the file priorities"
    )
    worker = Worker(factory, settings, {"qbittorrent": engine}, auto_inspect=False)

    await worker.tick()

    row = _row(factory, download_id)
    assert row.status == "failed"
    assert "FILE_SELECTION_FAILED" in (row.error or "")
    assert row.progress == 0.0


async def test_five_selection_failures_stop_retrying(
    db: tuple[Any, Any], settings: Settings
) -> None:
    _engine, factory = db
    download_id = _queued(factory)
    engine = SelectionEngine()
    engine.selection_error = EngineError(
        "QBT_FILE_SELECTION_PENDING", "qBittorrent did not return the file list in time"
    )
    worker = Worker(factory, settings, {"qbittorrent": engine}, auto_inspect=False)

    for _attempt in range(5):
        worker._selection_not_before.pop(download_id, None)  # type: ignore[attr-defined]
        await worker.tick()

    row = _row(factory, download_id)
    assert row.status == "failed"
    assert "FILE_SELECTION_FAILED" in (row.error or "")
    assert worker._selection_attempts.get(download_id) is None  # type: ignore[attr-defined]
