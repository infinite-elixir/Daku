"""Saved and search-result release repository.

A release row carries two independent facts:

* it exists (the user selected it, so it is a *recent* option);
* ``is_saved`` (the user explicitly asked to keep it).

The upsert never resets those on a metadata refresh: identity and intent survive
re-checking a magnet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from daku.models import Release
from daku.services.releases.metadata import load_metadata, resolved_from
from daku.utils import new_id, utc_now_iso

DEAD_AFTER_MISSES = 3

# The unique index is on (info_hash, COALESCE(episode_id, '')), so the upsert target
# must use the same expression. A plain (info_hash, episode_id) target would not match
# when episode_id is NULL (movies, whole-title magnets).
_CONFLICT_TARGET = (Release.info_hash, func.coalesce(Release.episode_id, text("''")))


def create_release(
    session: Session,
    *,
    title_id: str,
    episode_id: str | None = None,
    provider: str | None = None,
    info_hash: str | None = None,
    magnet: str | None = None,
    filename: str | None = None,
    quality: str | None = None,
    size_bytes: int | None = None,
    seeders: int | None = None,
    metadata_json: str | None = None,
    is_saved: bool = False,
    fetched_at: str | None = None,
) -> Release:
    now = utc_now_iso()
    row = Release(
        id=new_id(),
        title_id=title_id,
        episode_id=episode_id,
        provider=provider,
        info_hash=info_hash,
        magnet=magnet,
        filename=filename,
        quality=quality,
        size_bytes=size_bytes,
        seeders=seeders,
        metadata_json=metadata_json,
        is_saved=is_saved,
        saved_at=now if is_saved else None,
        fetched_at=fetched_at or now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_release(session: Session, release_id: str) -> Release | None:
    return session.get(Release, release_id)


def find_by_hash(
    session: Session,
    info_hash: str,
    *,
    episode_id: str | None = None,
) -> Release | None:
    """Find the one row for ``(info_hash, episode_id)``.

    ``episode_id=None`` means a title-level release (movies, whole packs), not
    "any episode".
    """

    statement = select(Release).where(Release.info_hash == info_hash)
    if episode_id is None:
        statement = statement.where(Release.episode_id.is_(None))
    else:
        statement = statement.where(Release.episode_id == episode_id)
    return session.scalar(statement.limit(1))


def upsert_release(
    session: Session,
    *,
    title_id: str,
    info_hash: str,
    episode_id: str | None = None,
    provider: str | None = None,
    magnet: str | None = None,
    filename: str | None = None,
    quality: str | None = None,
    size_bytes: int | None = None,
    seeders: int | None = None,
    metadata_json: str | None = None,
    is_saved: bool = False,
) -> tuple[Release, bool]:
    """Create or update one row per ``(info_hash, episode_id)``.

    Returns the row and whether it was created. ``is_saved``/``saved_at`` are only
    written on insert; the explicit save endpoints own that intent.
    """

    now = utc_now_iso()
    existing = find_by_hash(session, info_hash, episode_id=episode_id)
    values: dict[str, Any] = {
        "id": new_id(),
        "title_id": title_id,
        "episode_id": episode_id,
        "provider": provider,
        "info_hash": info_hash.lower(),
        "magnet": magnet,
        "filename": filename,
        "quality": quality,
        "size_bytes": size_bytes,
        "seeders": seeders,
        "metadata_json": metadata_json,
        "is_saved": is_saved,
        "saved_at": now if is_saved else None,
        "last_checked_at": now,
        "last_seen_seeders": seeders,
        "miss_count": 0,
        "dead": False,
        "fetched_at": now,
    }

    statement = sqlite_insert(Release).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=list(_CONFLICT_TARGET),
        set_={
            "provider": statement.excluded.provider,
            "magnet": statement.excluded.magnet,
            "filename": statement.excluded.filename,
            "quality": statement.excluded.quality,
            # A manual re-save may not know the size or seeder count: keep what we had.
            "size_bytes": func.coalesce(statement.excluded.size_bytes, Release.size_bytes),
            "seeders": func.coalesce(statement.excluded.seeders, Release.seeders),
            "metadata_json": func.coalesce(statement.excluded.metadata_json, Release.metadata_json),
            "last_checked_at": statement.excluded.last_checked_at,
            "last_seen_seeders": func.coalesce(
                statement.excluded.last_seen_seeders, Release.last_seen_seeders
            ),
            "miss_count": 0,
            "dead": False,
            "fetched_at": statement.excluded.fetched_at,
        },
    )
    session.execute(statement)
    session.commit()

    row = find_by_hash(session, info_hash.lower(), episode_id=episode_id)
    if row is None:  # pragma: no cover - only possible if the insert was rejected
        raise RuntimeError("release upsert did not persist a row")
    session.refresh(row)
    return row, existing is None


def list_releases(session: Session, title_id: str) -> list[Release]:
    return list(
        session.scalars(
            select(Release)
            .where(Release.title_id == title_id)
            .order_by(Release.is_saved.desc(), Release.seeders.desc().nulls_last(), Release.id)
        )
    )


def list_saved(
    session: Session,
    *,
    title_id: str | None = None,
    episode_id: str | None = None,
    include_dead: bool = True,
    dead: bool | None = None,
    qualities: Sequence[str] | None = None,
) -> list[Release]:
    """List explicitly saved releases, newest first, with optional filters."""

    statement = select(Release).where(Release.is_saved.is_(True))
    if title_id is not None:
        statement = statement.where(Release.title_id == title_id)
    if episode_id is not None:
        statement = statement.where(Release.episode_id == episode_id)
    if dead is not None:
        statement = statement.where(Release.dead.is_(dead))
    elif not include_dead:
        statement = statement.where(Release.dead.is_(False))
    statement = statement.order_by(
        Release.saved_at.desc().nulls_last(),
        Release.seeders.desc().nulls_last(),
        Release.id,
    )
    rows = list(session.scalars(statement))
    if qualities:
        wanted = {label.strip().lower() for label in qualities if label and label.strip()}
        if wanted:
            rows = [row for row in rows if (row.quality or "unknown").lower() in wanted]
    return rows


def save_release(
    session: Session,
    release_id: str,
    *,
    metadata_json: str | None = None,
) -> Release | None:
    row = session.get(Release, release_id)
    if row is None:
        return None
    row.is_saved = True
    row.saved_at = utc_now_iso()
    if metadata_json is not None:
        row.metadata_json = metadata_json
    session.commit()
    session.refresh(row)
    return row


def unsave_release(session: Session, release_id: str) -> Release | None:
    row = session.get(Release, release_id)
    if row is None:
        return None
    row.is_saved = False
    row.saved_at = None
    session.commit()
    session.refresh(row)
    return row


def record_seen(
    session: Session,
    release_id: str,
    *,
    seeders: int,
    size_bytes: int | None = None,
    checked_at: str | None = None,
) -> Release | None:
    """Record a successful re-check that found the hash upstream."""

    row = session.get(Release, release_id)
    if row is None:
        return None
    row.last_checked_at = checked_at or utc_now_iso()
    row.seeders = seeders
    row.last_seen_seeders = seeders
    if size_bytes is not None:
        row.size_bytes = size_bytes
    row.miss_count = 0
    row.dead = False
    session.commit()
    session.refresh(row)
    return row


def record_miss(
    session: Session,
    release_id: str,
    *,
    checked_at: str | None = None,
) -> Release | None:
    """Record a successful fetch that did *not* contain the hash.

    Only a successful upstream fetch may reach this function. A network failure must
    leave ``miss_count`` alone, otherwise an outage would mark every release dead.
    """

    row = session.get(Release, release_id)
    if row is None:
        return None
    row.last_checked_at = checked_at or utc_now_iso()
    row.miss_count += 1
    if row.miss_count >= DEAD_AFTER_MISSES:
        row.dead = True
    session.commit()
    session.refresh(row)
    return row


def record_check(session: Session, release_id: str, seeders: int | None) -> Release | None:
    """Backwards-compatible wrapper used by earlier callers."""

    if seeders is None:
        return record_miss(session, release_id)
    return record_seen(session, release_id, seeders=seeders)


def delete_release(session: Session, release_id: str) -> bool:
    row = session.get(Release, release_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def to_payload(row: Release) -> dict[str, Any]:
    """Serialise a stored release with the T-007 key names plus its row identity."""

    metadata = load_metadata(row.metadata_json)
    origin = resolved_from(row)
    return {
        "id": row.id,
        "release_id": row.id,
        "title_id": row.title_id,
        "episode_id": row.episode_id,
        "provider": row.provider,
        "info_hash": row.info_hash,
        "filename": row.filename,
        "quality": row.quality,
        "size_bytes": row.size_bytes,
        "seeders": row.seeders,
        "magnet": row.magnet,
        "metadata_json": metadata,
        "resolved_from": origin,
        "is_saved": row.is_saved,
        "saved_at": row.saved_at,
        "last_checked_at": row.last_checked_at,
        "last_seen_seeders": row.last_seen_seeders,
        "miss_count": row.miss_count,
        "dead": row.dead,
        "fetched_at": row.fetched_at,
    }
