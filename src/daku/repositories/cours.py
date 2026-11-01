"""Split-cour identity rows (T-052).

``title_seasons`` answers "which provider season supplies this library season".
``title_cours`` answers the finer question for a split cour: which AniList entry
supplies episodes 1-1 of that season, which supplies 2-12, and what provider season
do release names use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from daku.models import TitleCour
from daku.utils import new_id, utc_now_iso


def list_cours(session: Session, title_id: str) -> list[TitleCour]:
    return list(
        session.scalars(
            select(TitleCour)
            .where(TitleCour.title_id == title_id)
            .order_by(TitleCour.library_season, TitleCour.start_episode)
        )
    )


def replace_cours(
    session: Session, title_id: str, rows: Sequence[Mapping[str, Any]]
) -> list[TitleCour]:
    """Replace the whole cour map. Used only when a refresh resolves a new split."""

    session.execute(delete(TitleCour).where(TitleCour.title_id == title_id))
    now = utc_now_iso()
    for item in rows:
        session.add(
            TitleCour(
                id=new_id(),
                title_id=title_id,
                library_season=int(item["library_season"]),
                provider_season=int(item.get("provider_season") or 1),
                start_episode=int(item["start_episode"]),
                end_episode=int(item["end_episode"]),
                anilist_id=int(item["anilist_id"]),
                absolute_offset=int(item.get("absolute_offset") or 0),
                created_at=now,
                updated_at=now,
            )
        )
    session.flush()
    return list_cours(session, title_id)


def delete_cours(session: Session, title_id: str) -> None:
    session.execute(delete(TitleCour).where(TitleCour.title_id == title_id))


def cour_for_episode(
    session: Session, title_id: str, library_season: int, episode: int
) -> TitleCour | None:
    """The cour that owns one library episode, if a split map exists."""

    for row in list_cours(session, title_id):
        if row.library_season == library_season and row.start_episode <= episode <= row.end_episode:
            return row
    return None


__all__ = [
    "cour_for_episode",
    "delete_cours",
    "list_cours",
    "replace_cours",
]
