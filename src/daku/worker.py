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
            rows = [
                _row(row)
                for row in downloads_repo.list_downloads(session)
                if row.status in POLLED_STATUSES
            ]
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

            if engine_name == "aria2" and (queued or active) and self._supervisor is not None:
                if not self._supervisor.available:
                    self._set_error(group, "aria2c is not installed")
                    continue
                await self._supervisor.ensure_started()

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

            request = AddRequest(
                external_id=row.external_id,
                url=url,
                save_path=row.save_path or str(self._settings.downloads_dir),
                filename=self._release_filename(row.release_id),
                tag=self._release_title_id(row.release_id),
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
                status="active",
                external_id=external_id,
                error=None,
                progress=0.0,
            )
            self._touch_external_id(row, external_id)
            self._event(row.id, "active", type="started")
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
                await inspect_download(session, download, settings=self._settings)
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
