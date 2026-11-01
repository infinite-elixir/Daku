"""Destructive deletion preflight.

Removing a download with ``delete_files=true`` asks the engine to remove its copy
of every file. That is only safe when Daku has already produced a verified library
copy. Until the organiser exists, a finished download is still the only copy, so
the preflight refuses the deletion instead of silently destroying it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from daku.config import Settings
from daku.models import Download, MediaFile
from daku.repositories import media_files as media_files_repo
from daku.repositories import releases as releases_repo
from daku.services.engines.base import DownloadEngine, EngineFile

logger = logging.getLogger(__name__)

UNSAFE_REASON = "no_library_copy"
ENGINE_UNAVAILABLE_REASON = "engine_files_unavailable"


@dataclass(frozen=True, slots=True)
class DeletionAssessment:
    """The read-only answer to "may the engine delete its copy?"."""

    safe: bool
    unsafe_count: int = 0
    checked_count: int = 0
    file_count: int = 0
    reason: str | None = None


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _under(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _library_roots(settings: Settings) -> tuple[Path, ...]:
    return (
        _resolved(settings.movies_dir),
        _resolved(settings.tv_dir),
        _resolved(settings.anime_dir),
    )


def library_copy_is_safe(row: MediaFile, settings: Settings) -> bool:
    """Is ``lib_path`` a real file in a library root, independent of downloads?"""

    if not row.lib_path:
        return False
    try:
        lib_path = _resolved(row.lib_path)
        if not lib_path.is_file():
            return False
        if _under(lib_path, _resolved(settings.downloads_dir)):
            return False
        if not any(_under(lib_path, root) for root in _library_roots(settings)):
            return False
        if row.size_bytes is not None and lib_path.stat().st_size != row.size_bytes:
            return False
    except OSError:
        return False
    return True


def candidate_media_files(session: Session, row: Download) -> list[MediaFile]:
    """Files this queue entry has produced, including earlier detached rows."""

    attached = media_files_repo.list_by_download(session, row.id)
    rows = list(attached)
    seen = {item.id for item in rows}
    release = releases_repo.get_release(session, row.release_id) if row.release_id else None
    if release is not None:
        for candidate in media_files_repo.list_by_title(session, release.title_id):
            if candidate.download_id is None and candidate.id not in seen:
                rows.append(candidate)
                seen.add(candidate.id)
    return rows


def payload_deletion_safety(
    session: Session, row: Download, settings: Settings
) -> tuple[bool, str | None]:
    """Whether the UI may offer "also delete the downloaded files"."""

    if row.engine != "qbittorrent":
        return False, UNSAFE_REASON
    rows = candidate_media_files(session, row)
    if not rows:
        return False, UNSAFE_REASON
    if any(not library_copy_is_safe(item, settings) for item in rows):
        return False, UNSAFE_REASON
    return True, None


def _delete_paths(row: Download, settings: Settings, files: list[EngineFile]) -> set[Path]:
    base = Path(row.save_path).expanduser() if row.save_path else settings.downloads_dir
    paths: set[Path] = set()
    for item in files:
        raw = str(item.path or "").strip()
        if not raw:
            continue
        candidate = Path(raw)
        paths.add(_resolved(candidate if candidate.is_absolute() else base / candidate))
    return paths


async def assess_delete_files(
    session: Session,
    row: Download,
    settings: Settings,
    engine: DownloadEngine,
) -> DeletionAssessment:
    """Refuse to let the engine delete a file that has no library copy.

    The check is read-only. For aria2 ``delete_files`` is ignored, so there is
    nothing to verify. A qBittorrent file list that cannot be fetched is treated
    as unsafe rather than guessed.
    """

    if row.engine != "qbittorrent":
        return DeletionAssessment(safe=True)

    files_method = getattr(engine, "files", None)
    if not callable(files_method):
        return DeletionAssessment(safe=False, reason=ENGINE_UNAVAILABLE_REASON)

    try:
        files: list[EngineFile] = list(await files_method(row.external_id))
    except Exception:
        logger.warning(
            "could not inspect torrent files before destructive delete",
            extra={"download_id": row.id, "engine": row.engine},
        )
        return DeletionAssessment(safe=False, reason=ENGINE_UNAVAILABLE_REASON)

    delete_paths = _delete_paths(row, settings, files)
    matched = [
        item
        for item in candidate_media_files(session, row)
        if item.src_path and _resolved(item.src_path) in delete_paths
    ]
    unsafe = [item for item in matched if not library_copy_is_safe(item, settings)]
    return DeletionAssessment(
        safe=not unsafe,
        unsafe_count=len(unsafe),
        checked_count=len(matched),
        file_count=len(files),
        reason=UNSAFE_REASON if unsafe else None,
    )


__all__ = [
    "DeletionAssessment",
    "assess_delete_files",
    "candidate_media_files",
    "library_copy_is_safe",
    "payload_deletion_safety",
]
