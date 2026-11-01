"""Read-only media-file surfaces: thumbnails (and, later, playback)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.errors import AppError
from daku.models import MediaFile
from daku.repositories import media_files as media_files_repo
from daku.services.player.mpv import MpvPlayer
from daku.services.thumbnails import THUMBNAIL_UNAVAILABLE, ThumbnailService

router = APIRouter(prefix="/api", tags=["media"])

CACHE_CONTROL = "private, max-age=86400"


def _unavailable(message: str) -> AppError:
    return AppError(code=THUMBNAIL_UNAVAILABLE, message=message, status_code=404)


@router.get("/media-files/{media_file_id}/thumbnail")
async def media_file_thumbnail(
    media_file_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    row = media_files_repo.get_media_file(session, media_file_id)
    if row is None:
        raise _unavailable("This media file does not exist")

    service: ThumbnailService = request.app.state.thumbnails
    etag = service.etag(row)
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if service.cache_path(row.id).is_file() and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    result = await service.get(row)
    return FileResponse(
        result.path,
        media_type="image/jpeg",
        headers={"ETag": result.etag, "Cache-Control": CACHE_CONTROL},
    )


def _play_error(code: str, message: str, status_code: int) -> AppError:
    return AppError(code=code, message=message, status_code=status_code)


def _library_path(row: MediaFile) -> Path:
    path = media_files_repo.usable_path(row)
    if path is None:
        raise _play_error("MEDIA_FILE_MISSING", "This media file has no recorded path", 404)
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        raise _play_error(
            "MEDIA_FILE_MISSING", "This media file is no longer on disk", 404
        ) from exc
    if not resolved.is_file():
        raise _play_error("MEDIA_FILE_MISSING", "This media file is no longer on disk", 404)
    return resolved


def _inside_library(path: Path, request: Request) -> bool:
    settings = request.app.state.settings
    # Until the organiser (T-013) lands, a completed download only exists in the engine's
    # download directory. Daku created that file, so it is as trustworthy as a filed one.
    roots = (
        settings.movies_dir,
        settings.tv_dir,
        settings.anime_dir,
        settings.downloads_dir,
    )
    for root in roots:
        try:
            if path.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):  # pragma: no cover - an unresolvable root
            continue
    return False


@router.post("/media-files/{media_file_id}/play", status_code=202)
async def media_file_play(
    media_file_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    """Launch the host's mpv with the filed library path.

    Only the media-file id is accepted. No path, flag or argv is read from the request.
    """

    row = media_files_repo.get_media_file(session, media_file_id)
    if row is None or row.title_id is None:
        raise _play_error("MEDIA_FILE_NOT_FOUND", "This media file does not exist", 404)

    path = _library_path(row)

    if not _inside_library(path, request):
        raise _play_error(
            "PATH_OUTSIDE_LIBRARY",
            "This file is outside the library roots and Daku's download directory",
            403,
        )

    settings = request.app.state.settings
    if not settings.mpv_enabled:
        raise _play_error(
            "MPV_DISABLED",
            "mpv playback is disabled; turn it on in Settings or set DAKU_MPV_ENABLED",
            409,
        )

    player: MpvPlayer = request.app.state.mpv_player
    if player.binary is None:
        raise _play_error("MPV_UNAVAILABLE", "mpv is not installed or is not on PATH", 503)

    result = await player.play(row.id, path)
    return JSONResponse(
        status_code=202,
        content={"media_file_id": result.media_file_id, "launched": result.launched},
    )


@router.get("/media-files/playback")
def playback_state(request: Request) -> dict[str, object]:
    """Live position reported by the host player, or ``null`` when it is idle.

    Read-only: the UI polls this while a title page is open to draw the seek bar.
    """

    player: MpvPlayer = request.app.state.mpv_player
    return {"active": player.current_progress()}
