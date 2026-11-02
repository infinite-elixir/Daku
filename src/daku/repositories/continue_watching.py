"""Continue Watching: the last thing played per title.

Playback progress is written by the host player's IPC reader (T-054). This module turns
those rows into one card per title: the in-progress file, or the next downloaded episode
once the last played file is finished. It never writes anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from daku.models import Episode, MediaFile, Title
from daku.repositories import media_files as media_files_repo

FINISHED_RATIO = 0.95
DEFAULT_LIMIT = 12
HIDDEN_WATCH_STATUSES = ("dropped", "on_hold")


@dataclass(frozen=True)
class ContinueWatchingEntry:
    title: Title
    file: MediaFile
    up_next: bool
    played_at: str


def _duration(row: MediaFile) -> float | None:
    duration = row.playback_duration_s or row.duration_s
    if duration is None or duration <= 0:
        return None
    return float(duration)


def _is_finished(row: MediaFile) -> bool | None:
    """``None`` when there is not enough information to decide."""

    duration = _duration(row)
    if duration is None or row.playback_position_s is None:
        return None
    return (row.playback_position_s / duration) >= FINISHED_RATIO


def _next_episode_file(
    session: Session,
    *,
    title_id: str,
    current: Episode,
) -> MediaFile | None:
    """The next downloaded episode after ``current``, ignoring Season 0 specials."""

    candidates: list[tuple[tuple[int, int, int, str, str], MediaFile]] = []
    for row in media_files_repo.list_by_title(session, title_id):
        if row.episode_id is None:
            continue
        episode = session.get(Episode, row.episode_id)
        if episode is None or episode.season == 0:
            continue
        if (episode.season, episode.episode) <= (current.season, current.episode):
            continue
        if not media_files_repo.is_present(row):
            continue
        order = (
            episode.season,
            episode.episode,
            0 if row.is_primary else 1,
            row.created_at or "",
            row.id,
        )
        candidates.append((order, row))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def list_continue_watching(
    session: Session,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[ContinueWatchingEntry]:
    """Newest played file per title, capped, with next-episode follow-ups."""

    rows = session.execute(
        select(MediaFile, Episode, Title)
        .join(Title, MediaFile.title_id == Title.id)
        .outerjoin(Episode, MediaFile.episode_id == Episode.id)
        .where(
            MediaFile.playback_position_s.is_not(None),
            MediaFile.playback_position_s > 0,
            MediaFile.playback_updated_at.is_not(None),
            or_(
                Title.watch_status.is_(None),
                Title.watch_status.not_in(HIDDEN_WATCH_STATUSES),
            ),
        )
    ).all()

    latest: dict[str, tuple[MediaFile, Episode | None, Title, str]] = {}
    for row, episode, title in rows:
        if episode is not None and episode.season == 0:
            continue
        if _duration(row) is None:
            continue
        if not media_files_repo.is_present(row):
            continue
        played_at = row.playback_updated_at or ""
        current = latest.get(title.id)
        if current is None or played_at > current[3]:
            latest[title.id] = (row, episode, title, played_at)

    entries: list[ContinueWatchingEntry] = []
    for card_file, card_episode, card_title, card_played_at in sorted(
        latest.values(), key=lambda item: item[3], reverse=True
    ):
        finished = _is_finished(card_file)
        if finished is None:
            continue
        if not finished:
            entries.append(
                ContinueWatchingEntry(
                    title=card_title, file=card_file, up_next=False, played_at=card_played_at
                )
            )
        elif card_episode is not None:
            follow_up = _next_episode_file(session, title_id=card_title.id, current=card_episode)
            if follow_up is not None:
                entries.append(
                    ContinueWatchingEntry(
                        title=card_title, file=follow_up, up_next=True, played_at=card_played_at
                    )
                )
        if len(entries) >= limit:
            break
    return entries
