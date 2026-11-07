"""Continue Watching: the last thing played per title, plus fresh downloads.

Playback progress is written by the host player's IPC reader (T-054). This module turns
those rows into one card per title: the in-progress file, or the next downloaded episode
once the last played file is finished. A title with no playback history still appears when
it has downloaded files, using its earliest unwatched episode (or primary movie file).
It never writes anything.
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
    played_at: str | None
    is_new: bool
    sort_at: str


@dataclass
class _FreshBucket:
    title: Title
    episodes: list[tuple[tuple[int, int, int, str, str], MediaFile]]
    loose: list[tuple[tuple[int, str, str], MediaFile]]
    latest_created: str = ""


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
    """The next downloaded, unwatched episode after ``current``; Season 0 is ignored."""

    candidates: list[tuple[tuple[int, int, int, str, str], MediaFile]] = []
    for row in media_files_repo.list_by_title(session, title_id):
        if row.episode_id is None:
            continue
        episode = session.get(Episode, row.episode_id)
        if episode is None or episode.season == 0 or episode.watched:
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


def _fresh_download_entries(
    session: Session,
    *,
    excluded_title_ids: set[str],
) -> list[ContinueWatchingEntry]:
    """One unwatched file per title that has downloads but no Continue Watching card.

    For a series this is the earliest downloaded episode the user has not marked
    watched, so a freshly downloaded season does not jump to its last episode. For a
    movie it is the primary file. A removed file never produces a card.
    """

    statement = (
        select(MediaFile, Episode, Title)
        .join(Title, MediaFile.title_id == Title.id)
        .outerjoin(Episode, MediaFile.episode_id == Episode.id)
        .where(
            or_(
                Title.watch_status.is_(None),
                Title.watch_status.not_in(HIDDEN_WATCH_STATUSES),
            ),
        )
    )
    if excluded_title_ids:
        statement = statement.where(MediaFile.title_id.not_in(excluded_title_ids))

    grouped: dict[str, _FreshBucket] = {}
    for row, episode, title in session.execute(statement).all():
        if not media_files_repo.is_present(row):
            continue
        if episode is not None:
            if episode.season == 0 or episode.watched:
                continue
        elif not row.is_primary:
            # Extras and sidecars have no episode; only the primary file represents a movie.
            continue

        bucket = grouped.setdefault(title.id, _FreshBucket(title=title, episodes=[], loose=[]))
        created_at = row.created_at or ""
        if created_at > bucket.latest_created:
            bucket.latest_created = created_at
        if episode is not None:
            bucket.episodes.append(
                (
                    (
                        episode.season,
                        episode.episode,
                        0 if row.is_primary else 1,
                        created_at,
                        row.id,
                    ),
                    row,
                )
            )
        else:
            bucket.loose.append(((0 if row.is_primary else 1, created_at, row.id), row))

    entries: list[ContinueWatchingEntry] = []
    for bucket in grouped.values():
        pool = bucket.episodes if bucket.episodes else bucket.loose
        if not pool:
            continue
        pool.sort(key=lambda pair: pair[0])
        entries.append(
            ContinueWatchingEntry(
                title=bucket.title,
                file=pool[0][1],
                up_next=False,
                played_at=None,
                is_new=True,
                sort_at=bucket.latest_created,
            )
        )
    return entries


def list_continue_watching(
    session: Session,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[ContinueWatchingEntry]:
    """Newest played file per title, capped, with next-episode and fresh-download follow-ups."""

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
    played_title_ids: set[str] = set()
    for row, episode, title in rows:
        if episode is not None and episode.season == 0:
            continue
        if not media_files_repo.is_present(row):
            continue
        # Any title with real playback history is not a "fresh download", even when the
        # row cannot produce a card (unknown duration, finished with no follow-up).
        played_title_ids.add(title.id)
        if _duration(row) is None:
            continue
        played_at = row.playback_updated_at or ""
        current = latest.get(title.id)
        if current is None or played_at > current[3]:
            latest[title.id] = (row, episode, title, played_at)

    entries: list[tuple[str, ContinueWatchingEntry]] = []
    occupied: set[str] = set(played_title_ids)
    for card_file, card_episode, card_title, card_played_at in sorted(
        latest.values(), key=lambda item: item[3], reverse=True
    ):
        finished = _is_finished(card_file)
        if finished is None:
            continue
        if not finished:
            entries.append(
                (
                    card_played_at,
                    ContinueWatchingEntry(
                        title=card_title,
                        file=card_file,
                        up_next=False,
                        played_at=card_played_at,
                        is_new=False,
                        sort_at=card_played_at,
                    ),
                )
            )
        elif card_episode is not None:
            follow_up = _next_episode_file(session, title_id=card_title.id, current=card_episode)
            if follow_up is not None:
                entries.append(
                    (
                        card_played_at,
                        ContinueWatchingEntry(
                            title=card_title,
                            file=follow_up,
                            up_next=True,
                            played_at=card_played_at,
                            is_new=False,
                            sort_at=card_played_at,
                        ),
                    )
                )
            else:
                # A finished title with nothing to follow up stays out of the strip.
                occupied.add(card_title.id)
        else:
            # A finished movie has no next file; do not re-add it as a fresh download.
            occupied.add(card_title.id)
        occupied.add(card_title.id)

    for entry in _fresh_download_entries(session, excluded_title_ids=occupied):
        entries.append((entry.sort_at, entry))

    entries.sort(key=lambda item: (item[0], item[1].title.id), reverse=True)
    return [entry for _sort_at, entry in entries[:limit]]
