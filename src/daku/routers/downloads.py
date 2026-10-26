"""Download queue surface.

``POST /api/downloads`` accepts either a saved release (torrent → qBittorrent) or a
direct URL (→ aria2). The two share the same ``downloads`` row and state machine.

Routers never write progress. Pause/resume/cancel/retry call the engine and move the
row to the matching state; the worker owns polling and transitions afterwards.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.errors import AppError
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import releases as releases_repo
from daku.services.downloads_view import download_payload
from daku.services.engines.aria2 import is_torrent_url
from daku.services.engines.base import AddRequest, DownloadEngine, EngineError
from daku.services.queue import check_disk_space, queue_direct, queue_release

router = APIRouter(prefix="/api", tags=["downloads"])

SIZE_PROBE_TIMEOUT_SECONDS = 3.0
TERMINAL_STATUSES = frozenset({"cancelled", "available", "organizing"})


class StartDownloadRequest(BaseModel):
    release_id: str | None = None
    url: str | None = None
    filename: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> StartDownloadRequest:
        if bool(self.release_id) == bool(self.url):
            raise ValueError("provide exactly one of release_id or url")
        return self


def _payload(session: Session, row: Download) -> dict[str, Any]:
    return download_payload(session, row)


def _publish(request: Request, event_type: str, payload: dict[str, Any]) -> None:
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        bus.publish(event_type, payload)


def _engine(request: Request, name: str) -> DownloadEngine:
    mapping = {
        "qbittorrent": getattr(request.app.state, "qbt_engine", None),
        "aria2": getattr(request.app.state, "aria2_engine", None),
    }
    engine = mapping.get(name)
    if engine is None:
        raise AppError(
            code="ENGINE_UNAVAILABLE",
            message=f"The {name} engine is not running",
            status_code=503,
        )
    return engine


async def _probe_content_length(client: httpx.AsyncClient, url: str) -> int | None:
    """Best-effort Content-Length for the disk preflight; unknown is not an error."""

    try:
        response = await client.head(url, follow_redirects=True, timeout=SIZE_PROBE_TIMEOUT_SECONDS)
    except (httpx.HTTPError, ValueError):
        return None
    raw = response.headers.get("content-length")
    try:
        length = int(raw) if raw is not None else None
    except ValueError:
        return None
    return length if length and length > 0 else None


def _clean_filename(value: str | None) -> str | None:
    if not value:
        return None
    name = value.replace("\x00", "").replace("/", "_").strip()
    return name or None


async def _start_direct(
    request: Request,
    session: Session,
    body: StartDownloadRequest,
) -> Download:
    url = (body.url or "").strip()
    if not url:
        raise AppError(
            code="DOWNLOAD_URL_REQUIRED", message="A download URL is required", status_code=422
        )
    if is_torrent_url(url):
        raise AppError(
            code="ARIA2_TORRENT_FORBIDDEN",
            message="Magnet and .torrent URLs must go to the torrent engine, not aria2",
            status_code=422,
        )

    settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.http_client
    expected_bytes = await _probe_content_length(client, url)
    check_disk_space(settings.downloads_dir, expected_bytes)

    engine = _engine(request, "aria2")
    supervisor = getattr(request.app.state, "aria2_supervisor", None)
    health = await engine.health()
    if not health.ok:
        started = supervisor is not None and await supervisor.ensure_started()
        if not started:
            raise AppError(
                code="ARIA2_UNAVAILABLE",
                message=f"aria2 is not available: {health.detail}",
                status_code=503,
            )

    try:
        gid = await engine.add(
            AddRequest(
                external_id="",
                url=url,
                save_path=str(settings.downloads_dir.expanduser()),
                filename=_clean_filename(body.filename),
            )
        )
    except EngineError as exc:
        raise AppError(
            code=exc.code,
            message=exc.message,
            status_code=503 if exc.transient else 422,
        ) from exc

    return queue_direct(
        session,
        url=url,
        gid=gid,
        save_path=settings.downloads_dir,
        total_bytes=expected_bytes,
    )


@router.post("/downloads")
async def start_download(
    body: StartDownloadRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Turn a saved release or a direct URL into a download row."""

    if body.url is not None:
        row = await _start_direct(request, session, body)
        payload = _payload(session, row)
        _publish(request, "download.created", payload)
        response.status_code = 201
        return payload

    release = releases_repo.get_release(session, body.release_id or "")
    if release is None:
        raise AppError(
            code="RELEASE_NOT_FOUND",
            message=f"Release {body.release_id} does not exist",
            status_code=404,
        )
    if not release.magnet or not release.info_hash:
        raise AppError(
            code="RELEASE_NOT_DOWNLOADABLE",
            message="This release has no magnet to hand to a download engine",
            status_code=422,
        )

    settings = request.app.state.settings
    result = queue_release(session, release, save_path=settings.downloads_dir)
    if result.created:
        payload = _payload(session, result.download)
        _publish(request, "download.created", payload)
        response.status_code = 201
        return payload

    # The same info-hash is already tracked. Returning the existing row with 409
    # makes a double tap harmless and tells the UI to open the existing download.
    response.status_code = 409
    return _payload(session, result.download)


@router.get("/downloads")
def list_downloads(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    rows = downloads_repo.list_downloads(session)[:limit]
    return {"items": [_payload(session, row) for row in rows], "total": len(rows)}


def _get_or_404(session: Session, download_id: str) -> Download:
    row = downloads_repo.get_download(session, download_id)
    if row is None:
        raise AppError(
            code="DOWNLOAD_NOT_FOUND",
            message=f"Download {download_id} does not exist",
            status_code=404,
        )
    return row


@router.post("/downloads/{download_id}/pause")
async def pause_download(
    download_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _get_or_404(session, download_id)
    if row.status == "paused":
        return _payload(session, row)
    if row.status in TERMINAL_STATUSES or row.status == "failed":
        raise AppError(
            code="DOWNLOAD_NOT_ACTIVE",
            message=f"Download is {row.status} and cannot be paused",
            status_code=409,
        )
    if row.status == "active":
        try:
            await _engine(request, row.engine).pause(row.external_id)
        except EngineError as exc:
            raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
    updated = downloads_repo.update_download(
        session, row.id, status="paused", speed_bps=None, eta_seconds=None, error=None
    )
    payload = _payload(session, updated or row)
    _publish(request, "download.updated", payload)
    return payload


@router.post("/downloads/{download_id}/resume")
async def resume_download(
    download_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _get_or_404(session, download_id)
    if row.status in {"active", "queued", "complete", "organizing", "available"}:
        return _payload(session, row)
    if row.status != "paused":
        raise AppError(
            code="DOWNLOAD_NOT_PAUSED",
            message=f"Download is {row.status} and cannot be resumed",
            status_code=409,
        )
    try:
        await _engine(request, row.engine).resume(row.external_id)
    except EngineError as exc:
        raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
    updated = downloads_repo.update_download(session, row.id, status="active", error=None)
    payload = _payload(session, updated or row)
    _publish(request, "download.updated", payload)
    return payload


@router.post("/downloads/{download_id}/cancel")
async def cancel_download(
    download_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _get_or_404(session, download_id)
    if row.status == "cancelled":
        return _payload(session, row)
    if row.status not in {"queued", "active", "paused", "failed", "complete"}:
        raise AppError(
            code="DOWNLOAD_NOT_CANCELLABLE",
            message=f"Download is {row.status} and cannot be cancelled",
            status_code=409,
        )
    try:
        await _engine(request, row.engine).remove(row.external_id, delete_files=False)
    except EngineError as exc:
        # Losing contact with the engine must not leave the row un-cancellable.
        if not exc.transient:
            raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
    updated = downloads_repo.update_download(
        session,
        row.id,
        status="cancelled",
        speed_bps=None,
        eta_seconds=None,
        error=None,
    )
    payload = _payload(session, updated or row)
    _publish(request, "download.updated", payload)
    return payload


@router.post("/downloads/{download_id}/inspect")
async def inspect_completed_download(
    download_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Probe the download and persist its media_files rows."""

    from daku.services.organize.select import inspect_download

    row = _get_or_404(session, download_id)
    settings = request.app.state.settings
    result = await inspect_download(session, row, settings=settings)
    _publish(request, "download.updated", _payload(session, row))
    return result.to_dict()


@router.post("/downloads/{download_id}/retry")
async def retry_download(
    download_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _get_or_404(session, download_id)
    if row.status in {"queued", "active"}:
        return _payload(session, row)
    if row.status not in {"failed", "cancelled"}:
        raise AppError(
            code="DOWNLOAD_NOT_RETRYABLE",
            message=f"Download is {row.status} and cannot be retried",
            status_code=409,
        )
    engine = _engine(request, row.engine)
    # Clear the dead engine-side entry so the worker can add it again cleanly.
    with contextlib.suppress(EngineError):
        await engine.remove(row.external_id, delete_files=False)
    updated = downloads_repo.update_download(
        session,
        row.id,
        status="queued",
        progress=0.0,
        downloaded_bytes=0,
        speed_bps=None,
        eta_seconds=None,
        error=None,
    )
    payload = _payload(session, updated or row)
    _publish(request, "download.updated", payload)
    return payload
