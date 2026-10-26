"""Download tracking repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import Download
from daku.utils import new_id, utc_now_iso

DOWNLOAD_STATUSES = frozenset(
    {"queued", "active", "paused", "complete", "organizing", "available", "failed", "cancelled"}
)


def create_download(
    session: Session,
    *,
    source: str,
    engine: str,
    external_id: str,
    release_id: str | None = None,
    status: str = "queued",
    save_path: str | None = None,
    url: str | None = None,
    total_bytes: int | None = None,
) -> Download:
    now = utc_now_iso()
    row = Download(
        id=new_id(),
        release_id=release_id,
        source=source,
        engine=engine,
        external_id=external_id,
        status=status,
        save_path=save_path,
        url=url,
        total_bytes=total_bytes,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_download(session: Session, download_id: str) -> Download | None:
    return session.get(Download, download_id)


def find_by_external_id(session: Session, *, engine: str, external_id: str) -> Download | None:
    return session.scalar(
        select(Download)
        .where(Download.engine == engine, Download.external_id == external_id)
        .limit(1)
    )


def list_downloads(session: Session, *, status: str | None = None) -> list[Download]:
    statement = select(Download).order_by(Download.created_at.desc(), Download.id)
    if status is not None:
        statement = statement.where(Download.status == status)
    return list(session.scalars(statement))


def update_download(session: Session, download_id: str, **fields: Any) -> Download | None:
    allowed = {
        "release_id",
        "external_id",
        "status",
        "progress",
        "downloaded_bytes",
        "total_bytes",
        "speed_bps",
        "eta_seconds",
        "save_path",
        "url",
        "error",
    }
    row = session.get(Download, download_id)
    if row is None:
        return None
    if "status" in fields and fields["status"] not in DOWNLOAD_STATUSES:
        raise ValueError(f"unknown download status: {fields['status']}")
    for key, value in fields.items():
        if key in allowed:
            setattr(row, key, value)
    row.updated_at = utc_now_iso()
    session.commit()
    session.refresh(row)
    return row


def delete_download(session: Session, download_id: str) -> bool:
    row = session.get(Download, download_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True
