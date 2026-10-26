"""Media file repository. Exactly one file per download may be primary."""

from __future__ import annotations

import json

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from daku.models import MediaFile
from daku.utils import new_id, utc_now_iso


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
