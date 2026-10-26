"""Per-season provider structure repository (T-037)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from daku.models import TitleSeason
from daku.utils import utc_now_iso


def list_seasons(session: Session, title_id: str) -> list[TitleSeason]:
    return list(
        session.scalars(
            select(TitleSeason)
            .where(TitleSeason.title_id == title_id)
            .order_by(TitleSeason.library_season)
        )
    )


def get_season(session: Session, title_id: str, library_season: int) -> TitleSeason | None:
    return session.get(TitleSeason, {"title_id": title_id, "library_season": library_season})


def season_anilist_id(session: Session, title_id: str, library_season: int) -> int | None:
    row = get_season(session, title_id, library_season)
    return row.anilist_id if row is not None else None


def _values(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": str(row["source"]),
        "source_id": str(row["source_id"]),
        "provider_season": int(row["provider_season"]),
        "anilist_id": None if row.get("anilist_id") is None else int(row["anilist_id"]),
        "absolute_offset": int(row.get("absolute_offset") or 0),
    }


def replace_seasons(
    session: Session, title_id: str, rows: Sequence[Mapping[str, Any]]
) -> list[TitleSeason]:
    """Replace the whole structure. Used by Add and the explicit rebuild action."""

    session.execute(delete(TitleSeason).where(TitleSeason.title_id == title_id))
    return upsert_seasons(session, title_id, rows, delete_missing=False)


def upsert_seasons(
    session: Session,
    title_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    delete_missing: bool = False,
) -> list[TitleSeason]:
    """Insert/update season rows. Refresh never deletes rows by default."""

    existing = {row.library_season: row for row in list_seasons(session, title_id)}
    now = utc_now_iso()
    seen: set[int] = set()
    for item in rows:
        library_season = int(item["library_season"])
        seen.add(library_season)
        row = existing.get(library_season)
        if row is None:
            row = TitleSeason(
                title_id=title_id,
                library_season=library_season,
                created_at=now,
                updated_at=now,
                **_values(item),
            )
            session.add(row)
            continue
        for field, value in _values(item).items():
            setattr(row, field, value)
        row.updated_at = now
    if delete_missing:
        for library_season, row in existing.items():
            if library_season not in seen:
                session.delete(row)
    session.commit()
    return list_seasons(session, title_id)


def delete_seasons(session: Session, title_id: str) -> None:
    session.execute(delete(TitleSeason).where(TitleSeason.title_id == title_id))
    session.commit()


__all__ = [
    "delete_seasons",
    "get_season",
    "list_seasons",
    "replace_seasons",
    "season_anilist_id",
    "upsert_seasons",
]
