"""The download worker loop.

The worker is the only component that writes download progress and status. Routers
enqueue rows and engine-control requests; the worker polls engines every
``worker_poll_seconds`` (2 s by default), applies the state machine and records a
status event whenever a row changes state.

Transient engine failures (qBittorrent restarted, aria2 not up yet) never destroy a
row: it stays active with ``error`` set as the last error, exactly as the doctor
expects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.events import EventBus
from daku.repositories import downloads as downloads_repo
from daku.repositories import events as events_repo
from daku.services.downloads_view import download_payload
from daku.services.engines.base import AddRequest, DownloadEngine, EngineError, EngineStatus
from daku.services.engines.supervisor import Aria2Supervisor

logger = logging.getLogger(__name__)

POLLED_STATUSES = frozenset({"queued", "active"})
STATUS_EVENT = "download.status"


@dataclass(slots=True)
class _Row:
    id: str
    engine: str
    external_id: str
    status: str
    save_path: str | None
    release_id: str | None
    url: str | None
    error: str | None
    selected_files_json: str | None = None
    file_match_json: str | None = None


@dataclass(slots=True)
class _Selection:
    indices: list[int] | None = None
    paths: list[str] | None = None
    pending: bool = False


def _parse_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _selection(download: Any) -> _Selection:
    raw = _parse_json(download.selected_files_json)
    indices = (
        [item for item in raw if isinstance(item, int)]
        if isinstance(raw, list) and all(isinstance(item, int) for item in raw)
        else None
    )
    match = _parse_json(download.file_match_json)
    paths: list[str] | None = None
    pending = False
    if isinstance(match, dict):
        raw_paths = match.get("file_paths")
        if isinstance(raw_paths, list):
            paths = [item for item in raw_paths if isinstance(item, str)] or None
        pending = bool(match.get("selection_pending"))
    return _Selection(indices=indices, paths=paths, pending=pending)


def _row(download: Any) -> _Row:
    return _Row(
        id=download.id,
        engine=download.engine,
        external_id=download.external_id,
        status=download.status,
        save_path=download.save_path,
        release_id=download.release_id,
        url=download.url,
        error=download.error,
        selected_files_json=download.selected_files_json,
        file_match_json=download.file_match_json,
    )


class Worker:
    """Poll active downloads and move them through the Daku state machine."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        engines: dict[str, DownloadEngine],
        *,
        supervisor: Aria2Supervisor | None = None,
        interval: float | None = None,
        bus: EventBus | None = None,
        auto_inspect: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._engines = engines
        self._supervisor = supervisor
        self._bus = bus
        self.auto_inspect = auto_inspect
        self.interval = interval if interval is not None else settings.worker_poll_seconds
        self._last_publish: dict[str, float] = {}
        self._selection_attempts: dict[str, int] = {}
        self._selection_not_before: dict[str, float] = {}

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("worker started", extra={"interval_s": self.interval})
        while not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - a tick must never kill the loop
                logger.exception("worker tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                continue
        logger.info("worker stopped")

    async def tick(self) -> None:
        with self._session_factory() as session:
            rows: list[_Row] = []
            for download in downloads_repo.list_downloads(session):
                pending_selection = download.status == "paused" and _selection(download).pending
                if download.status in POLLED_STATUSES or pending_selection:
                    rows.append(_row(download))
        if not rows:
            return

        grouped: dict[str, list[_Row]] = defaultdict(list)
        for row in rows:
            grouped[row.engine].append(row)

        for engine_name, group in grouped.items():
            engine = self._engines.get(engine_name)
            if engine is None:
                continue
            queued = [row for row in group if row.status == "queued"]
            active = [row for row in group if row.status == "active"]
            pending = [row for row in group if row.status == "paused"]

            if engine_name == "aria2" and (queued or active) and self._supervisor is not None:
                if not self._supervisor.available:
                    self._set_error(group, "aria2c is not installed")
                    continue
                await self._supervisor.ensure_started()

            if pending:
                await self._retry_selection(engine, pending)
            if queued:
                await self._start(engine, queued)
            if active:
                await self._poll(engine, active)

    # -- starting ------------------------------------------------------------

    async def _start(self, engine: DownloadEngine, rows: list[_Row]) -> None:
        for row in rows:
            url = row.url
            if url is None and row.release_id is not None:
                url = self._release_magnet(row.release_id)
            if not url:
                self._fail(row, "no source URL is stored for this download")
                continue

            # A restart can leave a torrent tracked by qBittorrent while Daku still
            # thinks it is queued. Adopt it instead of adding a duplicate.
            if engine.name == "qbittorrent":
                adopted = await self._adopt(engine, row)
                if adopted:
                    continue

            selection = _selection(row)
            has_selection = selection.indices is not None
            request = AddRequest(
                external_id=row.external_id,
                url=url,
                save_path=row.save_path or str(self._settings.downloads_dir),
                filename=self._release_filename(row.release_id),
                tag=self._release_title_id(row.release_id),
                paused=has_selection,
            )
            try:
                external_id = await engine.add(request)
            except EngineError as exc:
                if exc.transient:
                    self._set_error([row], exc.message)
                else:
                    self._fail(row, exc.message)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                self._fail(row, str(exc))
                continue

            self._update(
                row.id,
                external_id=external_id,
                error=None,
                progress=0.0,
            )
            self._touch_external_id(row, external_id)

            if not has_selection:
                self._update(row.id, status="active")
                row.status = "active"
                self._event(row.id, "active", type="started")
                self._publish(row.id, force=True)
                continue

            # Close the race on versions that ignore the add-time pause flag. The
            # selection setter stops again (idempotent) before setting priorities.
            stop = getattr(engine, "stop", None)
            if stop is not None:
                try:
                    await stop(external_id)
                except EngineError as exc:
                    if exc.transient:
                        self._mark_selection_pending(row, exc.message)
                    else:
                        self._fail(row, f"{exc.code}: {exc.message}")
                    continue
            if not await self._apply_selection(engine, row):
                continue
            self._publish(row.id, force=True)

    async def _apply_selection(self, engine: DownloadEngine, row: _Row) -> bool:
        """Apply a known file selection; leave the torrent paused if it is not ready."""

        selection = _selection(row)
        if selection.indices is None:
            return True
        setter = getattr(engine, "set_file_selection", None)
        if setter is None:
            return True
        previous = row.status
        try:
            effective = await setter(
                row.external_id,
                selection.indices,
                paths=selection.paths,
            )
        except EngineError as exc:
            if exc.transient:
                self._mark_selection_pending(row, exc.message)
                return False
            self._fail(row, f"{exc.code}: {exc.message}")
            return False
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_selection_pending(row, str(exc))
            return False

        match = _parse_json(row.file_match_json)
        if not isinstance(match, dict):
            match = {}
        match.pop("selection_pending", None)
        if selection.paths:
            match["file_paths"] = selection.paths
        self._update(
            row.id,
            status="active",
            error=None,
            selected_files_json=json.dumps(effective),
            file_match_json=json.dumps(match, ensure_ascii=False) if match else None,
        )
        row.selected_files_json = json.dumps(effective)
        row.file_match_json = json.dumps(match, ensure_ascii=False) if match else None
        row.status = "active"
        row.error = None
        self._selection_attempts.pop(row.id, None)
        self._selection_not_before.pop(row.id, None)
        if previous != "active":
            self._event(row.id, "active", type="started")
        return True

    def _mark_selection_pending(self, row: _Row, message: str) -> None:
        match = _parse_json(row.file_match_json)
        if not isinstance(match, dict):
            match = {}
        match["selection_pending"] = True
        attempts = self._selection_attempts.get(row.id, 0) + 1
        self._selection_attempts[row.id] = attempts
        if attempts >= 5:
            self._selection_attempts.pop(row.id, None)
            self._selection_not_before.pop(row.id, None)
            self._fail(
                row,
                f"FILE_SELECTION_FAILED: could not restrict this torrent after "
                f"5 attempts: {message}",
            )
            return
        delay = min(60.0, float(2 ** min(attempts - 1, 6)))
        self._selection_not_before[row.id] = time.monotonic() + delay
        error = f"FILE_SELECTION_PENDING: {message}"
        self._update(
            row.id,
            status="paused",
            error=error,
            file_match_json=json.dumps(match, ensure_ascii=False),
            speed_bps=None,
            eta_seconds=None,
        )
        row.status = "paused"
        row.error = error
        row.file_match_json = json.dumps(match, ensure_ascii=False)
        self._publish(row.id, force=True)

    async def _retry_selection(self, engine: DownloadEngine, rows: list[_Row]) -> None:
        now = time.monotonic()
        for row in rows:
            not_before = self._selection_not_before.get(row.id, 0.0)
            if not_before > now:
                continue
            self._update(row.id, error=None)
            if await self._apply_selection(engine, row):
                self._publish(row.id, force=True)

    async def _adopt(self, engine: DownloadEngine, row: _Row) -> bool:
        try:
            statuses = await engine.poll([row.external_id])
        except EngineError as exc:
            if exc.transient:
                self._set_error([row], exc.message)
                return True
            return False
        status = self._status_for(statuses, row.external_id)
        if status is None:
            return False
        selection = _selection(row)
        if selection.pending or selection.indices is not None:
            # Never adopt a partially tracked selection as an active whole pack.
            await self._apply_selection(engine, row)
            return True
        if self._apply(row, status):
            await self._auto_inspect(row)
        return True

    # -- polling -------------------------------------------------------------

    async def _poll(self, engine: DownloadEngine, rows: list[_Row]) -> None:
        try:
            statuses = await engine.poll([row.external_id for row in rows])
        except EngineError as exc:
            self._set_error(rows, exc.message)
            return

        for row in rows:
            status = self._status_for(statuses, row.external_id)
            if status is None:
                if await self._readopt(engine, row):
                    continue
                self._fail(row, f"download is no longer known to {engine.name}")
                continue
            if (
                status.status == "failed"
                and row.url
                and status.error
                and "no longer exists" in status.error
                and await self._readopt(engine, row)
            ):
                continue
            if self._apply(row, status):
                await self._auto_inspect(row)

    async def _readopt(self, engine: DownloadEngine, row: _Row) -> bool:
        """Reconnect an aria2 row to a transfer that survived a restart."""

        adopt = getattr(engine, "adopt", None)
        if engine.name != "aria2" or not row.url or adopt is None:
            return False
        try:
            gid = await adopt(row.url)
        except EngineError:
            return False
        if not gid:
            return False
        self._update(
            row.id,
            external_id=gid,
            error="re-adopted after aria2 restart",
        )
        row.external_id = gid
        return True

    @staticmethod
    def _status_for(statuses: dict[str, EngineStatus], external_id: str) -> EngineStatus | None:
        return statuses.get(external_id) or statuses.get(external_id.lower())

    # -- persistence ---------------------------------------------------------

    def _update(self, download_id: str, **fields: Any) -> None:
        with self._session_factory() as session:
            downloads_repo.update_download(session, download_id, **fields)

    def _set_error(self, rows: list[_Row], message: str) -> None:
        for row in rows:
            if row.error == message:
                continue
            self._update(row.id, error=message)
            self._publish(row.id)

    def _fail(self, row: _Row, message: str) -> None:
        changed = row.status != "failed"
        self._update(row.id, status="failed", error=message, speed_bps=None, eta_seconds=None)
        if changed:
            self._event(row.id, "failed", type="failed", error=message)
        self._publish(row.id, force=changed)

    def _apply(self, row: _Row, status: EngineStatus) -> bool:
        """Persist an engine status; return True when the row just completed."""

        previous = row.status
        fields: dict[str, Any] = {
            "status": status.status,
            "progress": status.progress,
            "downloaded_bytes": status.downloaded_bytes,
            "speed_bps": status.speed_bps,
            "eta_seconds": status.eta_seconds,
        }
        if status.total_bytes is not None:
            fields["total_bytes"] = status.total_bytes
        if status.error:
            fields["error"] = status.error
        elif status.status in {"active", "complete"}:
            fields["error"] = None
        self._update(row.id, **fields)
        changed = previous != status.status
        if changed:
            self._event(row.id, status.status, type="changed")
        self._publish(row.id, force=changed)
        return status.status == "complete" and previous != "complete"

    # -- events --------------------------------------------------------------

    def _publish(self, download_id: str, *, force: bool = False) -> None:
        """Publish a download.updated event, throttled to 1/s per row."""

        if self._bus is None:
            return
        now = time.monotonic()
        last = self._last_publish.get(download_id)
        if not force and last is not None and now - last < 1.0:
            return
        self._last_publish[download_id] = now
        try:
            with self._session_factory() as session:
                row = downloads_repo.get_download(session, download_id)
                if row is None:
                    return
                payload = download_payload(session, row)
            self._bus.publish("download.updated", payload)
        except Exception:  # pragma: no cover - events must not stop the worker
            logger.exception("failed to publish download event")

    async def _auto_inspect(self, row: _Row) -> None:
        """Probe a just-completed download so media_files exist for T-013."""

        if not self.auto_inspect:
            return
        from pathlib import Path

        from daku.errors import AppError
        from daku.services.organize.select import inspect_download

        save_path = Path(row.save_path or self._settings.downloads_dir)
        if not save_path.is_dir():
            return
        try:
            with self._session_factory() as session:
                download = downloads_repo.get_download(session, row.id)
                if download is None:
                    return
                await inspect_download(
                    session,
                    download,
                    settings=self._settings,
                    engine=self._engines.get(row.engine),
                )
        except AppError as exc:
            self._update(row.id, error=f"{exc.code}: {exc.message}")
        except Exception:  # pragma: no cover - inspection must not kill the loop
            logger.exception("automatic inspection failed", extra={"download_id": row.id})
        finally:
            self._publish(row.id, force=True)

    @staticmethod
    def _touch_external_id(row: _Row, external_id: str) -> None:
        row.external_id = external_id

    def _event(self, download_id: str, status: str, *, type: str, **extra: Any) -> None:
        try:
            with self._session_factory() as session:
                events_repo.add_event(
                    session,
                    STATUS_EVENT,
                    {"download_id": download_id, "status": status, "type": type, **extra},
                )
        except Exception:  # pragma: no cover - events must not stop the worker
            logger.exception("failed to record download event")

    # -- release lookups -----------------------------------------------------

    def _release_magnet(self, release_id: str) -> str | None:
        with self._session_factory() as session:
            from daku.repositories import releases as releases_repo

            release = releases_repo.get_release(session, release_id)
            return release.magnet if release is not None else None

    def _release_filename(self, release_id: str | None) -> str | None:
        if release_id is None:
            return None
        with self._session_factory() as session:
            from daku.repositories import releases as releases_repo

            release = releases_repo.get_release(session, release_id)
            return release.filename if release is not None else None

    def _release_title_id(self, release_id: str | None) -> str | None:
        if release_id is None:
            return None
        with self._session_factory() as session:
            from daku.repositories import releases as releases_repo

            release = releases_repo.get_release(session, release_id)
            return release.title_id if release is not None else None
