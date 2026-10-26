"""Magnet metadata inspection endpoints.

Both endpoints are read-only: they never write a release, a download or a media file.
The probe itself fetches torrent metadata only.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.errors import AppError
from daku.repositories import releases as releases_repo
from daku.services.torrents.probe import (
    InvalidMagnet,
    ProbeBusy,
    ProbeError,
    ProbeFailed,
    ProbeService,
    ProbeTimeout,
    ProbeUnavailable,
)

router = APIRouter(prefix="/api", tags=["magnets"])

_ERROR_STATUS: dict[type[ProbeError], int] = {
    InvalidMagnet: 400,
    ProbeUnavailable: 503,
    ProbeTimeout: 504,
    ProbeBusy: 429,
    ProbeFailed: 502,
}


class InspectMagnetRequest(BaseModel):
    magnet: str = Field(min_length=1)


def _probe_service(request: Request) -> ProbeService:
    probe: ProbeService | None = getattr(request.app.state, "probe", None)
    if probe is None:
        raise AppError(
            code="PROBE_UNAVAILABLE",
            message="magnet probing is not configured",
            status_code=503,
        )
    return probe


def _translate(exc: ProbeError) -> AppError:
    status = _ERROR_STATUS.get(type(exc), 502)
    if isinstance(exc, ProbeUnavailable):
        message = "File list needs the libtorrent extra (install daku[probe])"
    else:
        message = exc.message
    return AppError(code=exc.code, message=message, status_code=status)


async def _metadata_payload(
    probe: ProbeService,
    magnet: str | None,
    *,
    release_id: str | None = None,
) -> dict[str, Any]:
    if not magnet:
        raise AppError(
            code="INVALID_MAGNET",
            message="This release has no stored magnet",
            status_code=400,
        )
    try:
        metadata = await probe.probe(magnet)
    except ProbeError as exc:
        raise _translate(exc) from exc
    payload = metadata.to_dict()
    payload["source"] = "probe"
    if release_id is not None:
        payload["release_id"] = release_id
    return payload


@router.post("/magnets/inspect")
async def inspect_magnet(body: InspectMagnetRequest, request: Request) -> dict[str, Any]:
    """Return the file list for a pasted magnet without touching the database."""

    return await _metadata_payload(_probe_service(request), body.magnet)


@router.get("/releases/{release_id}/files")
async def release_files(
    release_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Probe the magnet stored on a release, even if it was never downloaded."""

    row = releases_repo.get_release(session, release_id)
    if row is None:
        raise AppError(
            code="RELEASE_NOT_FOUND",
            message=f"Release {release_id} does not exist",
            status_code=404,
        )
    return await _metadata_payload(
        _probe_service(request),
        row.magnet,
        release_id=row.id,
    )
