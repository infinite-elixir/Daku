"""Media file repository. Exactly one file per download may be primary."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from stat import S_ISREG

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from daku.models import MediaFile
from daku.utils import new_id, utc_now_iso


def usable_path(row: MediaFile) -> Path | None:
    """The path availability should inspect: library copy first, then download copy."""

    raw = row.lib_path or row.src_path
    return Path(raw) if raw else None


def is_present(row: MediaFile) -> bool:
    """Whether the recorded file is a regular file with the expected size."""

    path = usable_path(row)
    if path is None:
        return False
    try:
        info = path.stat()
    except (OSError, ValueError):
        return False
    if not S_ISREG(info.st_mode):
        return False
    return not (row.size_bytes is not None and info.st_size != row.size_bytes)


def set_playback(
    session: Session,
    media_file_id: str,
    position_s: float,
    duration_s: float | None,
) -> MediaFile | None:
    """Record the host player's position for a media file. Idempotent per call."""

    row = session.get(MediaFile, media_file_id)
    if row is None:
        return None
    row.playback_position_s = max(0.0, float(position_s))
    if duration_s is not None and duration_s > 0:
        row.playback_duration_s = float(duration_s)
    row.playback_updated_at = utc_now_iso()
    session.commit()
    return row


def create_media_file(
    session: Session,
    *,
    title_id: str,
    src_path: str,
    download_id: str | None = None,
    episode_id: str | None = None,
    lib_path: str | None = None,
    size_bytes: int | None = None,
    container: str | None = None,
    vcodec: str | None = None,
    acodec: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_s: float | None = None,
    is_primary: bool = False,
    sidecars: list[str] | None = None,
) -> MediaFile:
    row = MediaFile(
        id=new_id(),
        download_id=download_id,
        title_id=title_id,
        episode_id=episode_id,
        src_path=src_path,
        lib_path=lib_path,
        size_bytes=size_bytes,
        container=container,
        vcodec=vcodec,
        acodec=acodec,
        width=width,
        height=height,
        duration_s=duration_s,
        sidecars_json=json.dumps(sidecars, ensure_ascii=False) if sidecars else None,
        is_primary=is_primary,
        created_at=utc_now_iso(),
    )
    session.add(row)
    if is_primary:
        session.flush()
        _clear_siblings(session, row)
    session.commit()
    session.refresh(row)
    return row


def get_media_file(session: Session, media_file_id: str) -> MediaFile | None:
    return session.get(MediaFile, media_file_id)


def list_by_download(session: Session, download_id: str) -> list[MediaFile]:
    return list(session.scalars(select(MediaFile).where(MediaFile.download_id == download_id)))


def list_by_title(session: Session, title_id: str) -> list[MediaFile]:
    return list(session.scalars(select(MediaFile).where(MediaFile.title_id == title_id)))


def list_by_titles(session: Session, title_ids: Iterable[str]) -> list[MediaFile]:
    ids = list(dict.fromkeys(title_ids))
    if not ids:
        return []
    return list(session.scalars(select(MediaFile).where(MediaFile.title_id.in_(ids))))


def list_all(session: Session) -> list[MediaFile]:
    return list(session.scalars(select(MediaFile)))


def reconcile_title(session: Session, title_id: str) -> tuple[int, int]:
    """Delete only media rows whose file no longer exists; never touch disk."""

    rows = list_by_title(session, title_id)
    missing_ids = [row.id for row in rows if not is_present(row)]
    if missing_ids:
        session.execute(delete(MediaFile).where(MediaFile.id.in_(missing_ids)))
        session.commit()
    return len(missing_ids), len(rows) - len(missing_ids)


def _clear_siblings(session: Session, row: MediaFile) -> None:
    statement = update(MediaFile).where(MediaFile.id != row.id).values(is_primary=False)
    if row.download_id is not None:
        statement = statement.where(MediaFile.download_id == row.download_id)
    elif row.episode_id is not None:
        statement = statement.where(
            MediaFile.title_id == row.title_id, MediaFile.episode_id == row.episode_id
        )
    else:
        statement = statement.where(
            MediaFile.title_id == row.title_id, MediaFile.episode_id.is_(None)
        )
    session.execute(statement)


def set_primary(session: Session, media_file_id: str) -> MediaFile | None:
    """Promote one file and clear its siblings in a single transaction."""

    row = session.get(MediaFile, media_file_id)
    if row is None:
        return None
    _clear_siblings(session, row)
    row.is_primary = True
    session.commit()
    session.refresh(row)
    return row


def delete_media_file(session: Session, media_file_id: str) -> bool:
    row = session.get(MediaFile, media_file_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def delete_by_download(session: Session, download_id: str) -> int:
    """Remove rows for a download so a re-inspection is idempotent."""

    count = session.scalar(
        select(func.count()).select_from(MediaFile).where(MediaFile.download_id == download_id)
    )
    session.execute(delete(MediaFile).where(MediaFile.download_id == download_id))
    session.commit()
    return int(count or 0)


def sidecars(row: MediaFile) -> list[str]:
    if not row.sidecars_json:
        return []
    try:
        value = json.loads(row.sidecars_json)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []
