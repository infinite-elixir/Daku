"""Release → download handoff.

Pressing Download turns a saved release into a durable download row. For torrents the
row starts ``queued`` because the worker is the only component allowed to talk to
qBittorrent. For direct URLs the router has already obtained an aria2 GID, so the row
starts ``active`` with the GID as ``external_id``.

The magnet is never copied into ``downloads``; ``release_id`` is the durable link.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from daku.errors import AppError
from daku.models import Download, Release
from daku.repositories import downloads as downloads_repo

TORRENT_ENGINE = "qbittorrent"
DIRECT_ENGINE = "aria2"
DIRECT_SOURCE = "direct"
SPACE_MULTIPLIER = 2


@dataclass(slots=True)
class QueueResult:
    download: Download
    created: bool


def queue_release(
    session: Session,
    release: Release,
    *,
    save_path: Path | str,
    engine: str = TORRENT_ENGINE,
) -> QueueResult:
    """Create the download row for a release, deduplicating by info-hash."""

    info_hash = (release.info_hash or "").lower()
    existing = downloads_repo.find_by_external_id(session, engine=engine, external_id=info_hash)
    if existing is not None:
        if existing.release_id != release.id:
            updated = downloads_repo.update_download(session, existing.id, release_id=release.id)
            if updated is not None:
                existing = updated
        return QueueResult(download=existing, created=False)

    row = downloads_repo.create_download(
        session,
        source="torrent",
        engine=engine,
        external_id=info_hash,
        release_id=release.id,
        status="queued",
        save_path=str(Path(save_path).expanduser()),
        total_bytes=release.size_bytes,
    )
    return QueueResult(download=row, created=True)


def queue_direct(
    session: Session,
    *,
    url: str,
    gid: str,
    save_path: Path | str,
    total_bytes: int | None = None,
) -> Download:
    """Persist an already-accepted aria2 transfer."""

    return downloads_repo.create_download(
        session,
        source=DIRECT_SOURCE,
        engine=DIRECT_ENGINE,
        external_id=gid,
        status="active",
        save_path=str(Path(save_path).expanduser()),
        url=url,
        total_bytes=total_bytes,
    )


def free_bytes(path: Path | str) -> int:
    """Free bytes on the filesystem that holds ``path``.

    The directory may not exist yet, so walk up to the nearest existing parent.
    """

    candidate = Path(path).expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        return shutil.disk_usage(candidate).free
    except OSError:
        return 0


def check_disk_space(path: Path | str, expected_bytes: int | None) -> None:
    """Refuse a transfer when free space is below twice its expected size."""

    if not expected_bytes or expected_bytes <= 0:
        return
    required = expected_bytes * SPACE_MULTIPLIER
    available = free_bytes(path)
    if available < required:
        raise AppError(
            code="INSUFFICIENT_DISK_SPACE",
            message=(
                f"Need {required / (1024**3):.1f} GiB free for this download, "
                f"only {available / (1024**3):.1f} GiB available"
            ),
            status_code=507,
            detail={"required_bytes": required, "available_bytes": available},
        )
