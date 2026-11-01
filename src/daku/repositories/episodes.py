"""Episode repository."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import Episode, Title
from daku.services.metadata.models import EpisodeRef
from daku.utils import new_id, utc_now_iso


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


# --------------------------------------------------------------------- watch progress


def _empty_progress() -> dict[str, Any]:
    return {
        "watched_episode_count": 0,
        "watchable_episode_count": 0,
        "next_episode": None,
    }


def progress_map(session: Session, title_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Derive watched counts and the first unwatched non-special episode per title.

    Season 0 specials are excluded from both counts and ``next_episode``. Nothing is
    stored twice: these fields always come from the ``episodes`` rows.
    """

    ids = list(dict.fromkeys(title_ids))
    result = {title_id: _empty_progress() for title_id in ids}
    if not ids:
        return result

    rows = session.scalars(
        select(Episode)
        .where(Episode.title_id.in_(ids))
        .order_by(Episode.title_id, Episode.season, Episode.episode)
    )
    for row in rows:
        if row.season == 0:
            continue
        item = result.setdefault(str(row.title_id), _empty_progress())
        item["watchable_episode_count"] += 1
        if row.watched:
            item["watched_episode_count"] += 1
        elif item["next_episode"] is None:
            item["next_episode"] = {
                "season": row.season,
                "episode": row.episode,
                "name": row.name,
            }
    return result


def progress_for(session: Session, title_id: str) -> dict[str, Any]:
    return progress_map(session, [title_id]).get(title_id, _empty_progress())


def _apply_episode_transition(session: Session, title: Title) -> None:
    """Apply only the two automatic title transitions; manual choices stay sticky."""

    progress = progress_for(session, title.id)
    watched = int(progress["watched_episode_count"])
    total = int(progress["watchable_episode_count"])
    status = title.watch_status
    if watched >= 1 and status in (None, "planning"):
        status = "watching"
    if total > 0 and watched >= total and status in ("watching", "planning"):
        status = "completed"
    if status != title.watch_status:
        title.watch_status = status
        title.status_changed_at = utc_now_iso()


def set_episode_watched(
    session: Session,
    *,
    title: Title,
    episode: Episode,
    watched: bool,
) -> Episode:
    """Write one episode flag and recompute the automatic title transition."""

    now = utc_now_iso()
    episode.watched = watched
    episode.watched_at = now if watched else None
    session.flush()
    _apply_episode_transition(session, title)
    session.commit()
    session.refresh(episode)
    session.refresh(title)
    return episode


def set_title_watch_status(
    session: Session,
    *,
    title: Title,
    status: str | None,
    clear_progress: bool = False,
) -> Title:
    """Set the user's explicit status; only ``completed`` bulk-marks episodes."""

    previous = title.watch_status
    now = utc_now_iso()
    title.watch_status = status
    title.status_changed_at = now

    if status == "completed":
        for row in list_episodes(session, title.id):
            if row.season == 0:
                continue
            if not row.watched:
                row.watched = True
                row.watched_at = now
    elif previous == "completed" and clear_progress:
        for row in list_episodes(session, title.id):
            row.watched = False
            row.watched_at = None

    session.commit()
    session.refresh(title)
    return title
