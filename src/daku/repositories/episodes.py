"""Episode repository."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import Episode
from daku.services.metadata.models import EpisodeRef
from daku.utils import new_id


def create_episode(
    session: Session,
    *,
    title_id: str,
    season: int,
    episode: int,
    absolute: int | None = None,
    name: str | None = None,
    air_date: str | None = None,
    status: str = "missing",
) -> Episode:
    row = Episode(
        id=new_id(),
        title_id=title_id,
        season=season,
        episode=episode,
        absolute=absolute,
        name=name,
        air_date=air_date,
        status=status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def upsert_episode(
    session: Session,
    *,
    title_id: str,
    season: int,
    episode: int,
    absolute: int | None = None,
    name: str | None = None,
    air_date: str | None = None,
) -> Episode:
    row = find_episode(session, title_id=title_id, season=season, episode=episode)
    if row is None:
        return create_episode(
            session,
            title_id=title_id,
            season=season,
            episode=episode,
            absolute=absolute,
            name=name,
            air_date=air_date,
        )
    if absolute is not None:
        row.absolute = absolute
    if name is not None:
        row.name = name
    if air_date is not None:
        row.air_date = air_date
    session.commit()
    session.refresh(row)
    return row


def _absolute_for(
    ref: EpisodeRef,
    *,
    absolute_from_number: bool,
    season_offsets: dict[int, int] | None,
) -> int | None:
    if not absolute_from_number:
        return None
    if season_offsets is None:
        return ref.episode
    if ref.season == 0:
        return None
    offset = season_offsets.get(ref.season)
    if offset is None:
        return None
    return offset + ref.episode


def sync_episodes(
    session: Session,
    *,
    title_id: str,
    episodes: Sequence[EpisodeRef],
    absolute_from_number: bool = False,
    season_offsets: dict[int, int] | None = None,
) -> list[Episode]:
    """Insert missing ``(title_id, season, episode)`` rows without duplicating any.

    Existing rows keep their status; only metadata fields are refreshed when the
    provider now supplies them. ``season_offsets`` makes anime absolute numbers
    cumulative across seasons instead of restarting at one.
    """

    existing = {(row.season, row.episode): row for row in list_episodes(session, title_id)}
    for ref in episodes:
        key = (ref.season, ref.episode)
        absolute = _absolute_for(
            ref,
            absolute_from_number=absolute_from_number,
            season_offsets=season_offsets,
        )
        row = existing.get(key)
        if row is None:
            row = Episode(
                id=new_id(),
                title_id=title_id,
                season=ref.season,
                episode=ref.episode,
                absolute=absolute,
                name=ref.title,
                air_date=ref.air_date,
                status="missing",
            )
            session.add(row)
            existing[key] = row
            continue
        if ref.title is not None:
            row.name = ref.title
        if ref.air_date is not None:
            row.air_date = ref.air_date
        if absolute is not None:
            row.absolute = absolute

    session.commit()
    return list_episodes(session, title_id)


def find_episode(session: Session, *, title_id: str, season: int, episode: int) -> Episode | None:
    return session.scalar(
        select(Episode)
        .where(
            Episode.title_id == title_id,
            Episode.season == season,
            Episode.episode == episode,
        )
        .limit(1)
    )


def get_episode(session: Session, episode_id: str) -> Episode | None:
    return session.get(Episode, episode_id)


def list_episodes(session: Session, title_id: str) -> list[Episode]:
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.title_id == title_id)
            .order_by(Episode.season, Episode.episode)
        )
    )


def set_status(session: Session, episode_id: str, status: str) -> Episode | None:
    row = session.get(Episode, episode_id)
    if row is None:
        return None
    row.status = status
    session.commit()
    session.refresh(row)
    return row
