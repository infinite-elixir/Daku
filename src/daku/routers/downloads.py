"""Download queue surface.

``POST /api/downloads`` accepts either a saved release (torrent → qBittorrent) or a
direct URL (→ aria2). The two share the same ``downloads`` row and state machine.

Routers never write progress. Pause/resume/cancel/retry call the engine and move the
row to the matching state; the worker owns polling and transitions afterwards.
"""

from __future__ import annotations

import contextlib
import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
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
from daku.services.releases.selection import ResolveRequest, Selection, resolve_selection
from daku.services.torrents.probe import ProbeService

router = APIRouter(prefix="/api", tags=["downloads"])

SIZE_PROBE_TIMEOUT_SECONDS = 3.0
TERMINAL_STATUSES = frozenset({"cancelled", "available", "organizing"})


class StartDownloadRequest(BaseModel):
    release_id: str | None = None
    url: str | None = None
    filename: str | None = None
    episode_id: str | None = None
    file_indices: list[int] | None = None
    file_paths: list[str] | None = None
    allow_pack: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> StartDownloadRequest:
        if bool(self.release_id) == bool(self.url):
            raise ValueError("provide exactly one of release_id or url")
        return self


class PreviewSelectionRequest(BaseModel):
    release_id: str = Field(min_length=1)
    episode_id: str | None = None
    file_indices: list[int] | None = None
    file_paths: list[str] | None = None
    allow_pack: bool = False


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


def _selection_request(body: StartDownloadRequest | PreviewSelectionRequest) -> ResolveRequest:
    return ResolveRequest(
        episode_id=body.episode_id,
        file_indices=body.file_indices,
        file_paths=body.file_paths,
        allow_pack=body.allow_pack,
    )


def _probe(request: Request) -> ProbeService | None:
    service: ProbeService | None = getattr(request.app.state, "probe", None)
    return service


async def _resolve(
    request: Request,
    session: Session,
    release: Any,
    body: StartDownloadRequest | PreviewSelectionRequest,
) -> Selection:
    return await resolve_selection(
        session,
        release,
        probe=_probe(request),
        request=_selection_request(body),
    )


async def _apply_live_selection(
    request: Request,
    session: Session,
    row: Download,
    selection: Selection,
) -> None:
    """Push a new selection to an already-running torrent in place."""

    if row.status != "active" or selection.indices is None:
        return
    engine = getattr(request.app.state, "qbt_engine", None)
    setter = getattr(engine, "set_file_selection", None)
    if setter is None:
        return
    try:
        effective = await setter(
            row.external_id,
            selection.indices,
            paths=selection.paths,
        )
    except EngineError as exc:
        raise AppError(
            code=exc.code,
            message=exc.message,
            status_code=502 if exc.transient else 409,
        ) from exc
    if effective:
        match = dict(selection.match or {})
        match["file_paths"] = selection.paths or match.get("file_paths")
        downloads_repo.update_download(
            session,
            row.id,
            selected_files_json=json.dumps(effective),
            file_match_json=(json.dumps(match, ensure_ascii=False) if match else None),
        )


@router.post("/downloads/preview")
async def preview_selection(
    body: PreviewSelectionRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Resolve what a download would fetch without creating a row."""

    release = releases_repo.get_release(session, body.release_id)
    if release is None:
        raise AppError(
            code="RELEASE_NOT_FOUND",
            message=f"Release {body.release_id} does not exist",
            status_code=404,
        )
    selection = await _resolve(request, session, release, body)
    return selection.to_dict()


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

    selection = await _resolve(request, session, release, body)

    settings = request.app.state.settings
    result = queue_release(
        session,
        release,
        save_path=settings.downloads_dir,
        selected_files_json=selection.selected_files_json,
        file_match_json=selection.file_match_json,
    )
    if result.created:
        payload = _payload(session, result.download)
        payload.update(selection.to_dict())
        _publish(request, "download.created", payload)
        response.status_code = 201
        return payload

    await _apply_live_selection(request, session, result.download, selection)

    # The same info-hash is already tracked. Returning the existing row with 409
    # makes a double tap harmless and tells the UI to open the existing download.
    response.status_code = 409
    payload = _payload(session, result.download)
    payload.update(selection.to_dict())
    return payload


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
