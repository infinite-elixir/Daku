"""Library title repository.

Availability is always derived from ``media_files``; it is never set by hand. The
derivation lives here so routers stay thin.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from daku.models import Episode, MediaFile, Release, Title
from daku.repositories import media_files as media_files_repo
from daku.utils import new_id, utc_now_iso

KIND_ORDER = {"movie": 0, "series": 1, "anime": 2}
AVAILABILITY_ORDER = {"none": 0, "partial": 1, "complete": 2}


def create_title(
    session: Session,
    *,
    kind: str,
    title: str,
    year: int | None = None,
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    anilist_id: int | None = None,
    mal_id: int | None = None,
    overview: str | None = None,
    poster_url: str | None = None,
    banner_url: str | None = None,
    folder_name: str | None = None,
    metadata_source: str | None = None,
    wanted: bool = False,
) -> Title:
    now = utc_now_iso()
    row = Title(
        id=new_id(),
        kind=kind,
        title=title,
        year=year,
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        anilist_id=anilist_id,
        mal_id=mal_id,
        overview=overview,
        poster_url=poster_url,
        banner_url=banner_url,
        folder_name=folder_name,
        metadata_source=metadata_source,
        added_at=now,
        wanted=wanted,
        wanted_at=now if wanted else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_title(session: Session, title_id: str) -> Title | None:
    return session.get(Title, title_id)


def find_by_imdb_id(session: Session, imdb_id: str) -> Title | None:
    return session.scalar(select(Title).where(Title.imdb_id == imdb_id).limit(1))


def find_by_anilist_id(session: Session, anilist_id: int) -> Title | None:
    return session.scalar(select(Title).where(Title.anilist_id == anilist_id).limit(1))


def find_existing(
    session: Session,
    *,
    kind: str,
    source_id: str,
) -> Title | None:
    """Find a title by its stable provider identity."""

    if kind == "anime":
        try:
            anilist_id = int(source_id)
        except ValueError:
            return None
        return find_by_anilist_id(session, anilist_id)
    return find_by_imdb_id(session, source_id)


def list_library(
    session: Session,
    *,
    kind: str | None = None,
    wanted: bool | None = None,
) -> list[Title]:
    """List every library entry, including entries with no files or episodes."""

    statement = select(Title).order_by(Title.added_at.desc(), Title.title)
    if kind is not None:
        statement = statement.where(Title.kind == kind)
    if wanted is not None:
        statement = statement.where(Title.wanted.is_(wanted))
    return list(session.scalars(statement))


def query_library(
    session: Session,
    *,
    kind: str | None = None,
    wanted: bool | None = None,
    availability: str | None = None,
    has_files: bool | None = None,
    statuses: Sequence[str] | None = None,
    watched: bool | None = None,
    q: str | None = None,
    sort: str = "added",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Title], int]:
    """Return a filtered, stable-sorted page and the total match count."""

    statement = select(Title)
    if kind is not None:
        statement = statement.where(Title.kind == kind)
    if wanted is not None:
        statement = statement.where(Title.wanted.is_(wanted))
    if statuses:
        concrete = [status for status in statuses if status != "untracked"]
        clauses = []
        if concrete:
            clauses.append(Title.watch_status.in_(concrete))
        if "untracked" in statuses:
            clauses.append(Title.watch_status.is_(None))
        if clauses:
            statement = statement.where(or_(*clauses))
    if watched is not None:
        # Convenience only: ``watched`` means "completed", not a separate flag.
        statement = statement.where(
            Title.watch_status == "completed"
            if watched
            else or_(Title.watch_status.is_(None), Title.watch_status != "completed")
        )
    if has_files is not None:
        with_files = select(MediaFile.title_id)
        statement = statement.where(
            Title.id.in_(with_files) if has_files else Title.id.not_in(with_files)
        )
    if q:
        needle = f"%{q.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(Title.title).like(needle),
                func.lower(func.coalesce(Title.imdb_id, "")).like(needle),
            )
        )

    rows = list(session.scalars(statement))
    availability_by_id = availability_map(session, [row.id for row in rows])
    if availability is not None:
        rows = [row for row in rows if availability_by_id[row.id] == availability]

    if sort in {"added", "status_changed_at"}:
        # Two stable passes: newest first, then title/id for deterministic ties.
        rows.sort(key=lambda row: (row.title.casefold(), row.id))
        field = "added_at" if sort == "added" else "status_changed_at"
        rows.sort(key=lambda row: getattr(row, field) or "", reverse=True)
    else:
        rows.sort(key=lambda row: _sort_key(row, sort, availability_by_id))
    total = len(rows)
    return rows[offset : offset + limit], total


def _sort_key(
    title: Title,
    sort: str,
    availability_by_id: dict[str, str],
) -> tuple[Any, ...]:
    if sort == "title":
        return (title.title.casefold(), title.id)
    if sort == "year":
        # Newest first, unknown years last. The id keeps ties deterministic.
        return (-(title.year or 0), title.title.casefold(), title.id)
    if sort == "kind":
        return (KIND_ORDER.get(title.kind, 99), title.title.casefold(), title.id)
    if sort == "availability":
        value = availability_by_id.get(title.id, "none")
        return (AVAILABILITY_ORDER.get(value, 0), title.title.casefold(), title.id)
    return (title.title.casefold(), title.id)


def availability_map(session: Session, title_ids: Iterable[str]) -> dict[str, str]:
    """Derive ``none`` / ``partial`` / ``complete`` for each title in one pass.

    Only media rows whose file still exists count as available. All rows for the
    requested titles are fetched once, then stat'ed in this loop.
    """

    ids = list(dict.fromkeys(title_ids))
    if not ids:
        return {}

    episode_rows = session.execute(
        select(Episode.id, Episode.title_id, Episode.season).where(Episode.title_id.in_(ids))
    ).all()
    episode_counts: dict[str, int] = {}
    season_by_id: dict[str, int] = {}
    for episode_id, title_id, season in episode_rows:
        season_by_id[str(episode_id)] = int(season)
        if season != 0:
            key = str(title_id)
            episode_counts[key] = episode_counts.get(key, 0) + 1

    present_counts: dict[str, int] = {}
    available_episodes: dict[str, set[str]] = {}
    for row in _media_rows_by_title(session, ids):
        if not media_files_repo.is_present(row):
            continue
        present_counts[row.title_id] = present_counts.get(row.title_id, 0) + 1
        if row.episode_id is not None and season_by_id.get(row.episode_id, 0) != 0:
            available_episodes.setdefault(row.title_id, set()).add(row.episode_id)

    derived: dict[str, str] = {}
    for title_id in ids:
        episode_total = episode_counts.get(title_id, 0)
        available = len(available_episodes.get(title_id, ()))
        if episode_total == 0:
            derived[title_id] = "complete" if present_counts.get(title_id, 0) else "none"
        elif available == 0:
            derived[title_id] = "none"
        elif available >= episode_total:
            derived[title_id] = "complete"
        else:
            derived[title_id] = "partial"
    return derived


def _media_rows_by_title(session: Session, title_ids: Sequence[str]) -> list[MediaFile]:
    return media_files_repo.list_by_titles(session, title_ids)


def availability_for(session: Session, title: Title) -> str:
    return availability_map(session, [title.id]).get(title.id, "none")


def available_episode_ids(session: Session, title_id: str) -> set[str]:
    return {
        row.episode_id
        for row in media_files_repo.list_by_title(session, title_id)
        if row.episode_id is not None and media_files_repo.is_present(row)
    }


def missing_file_count(session: Session, title_id: str) -> int:
    return sum(
        1
        for row in media_files_repo.list_by_title(session, title_id)
        if not media_files_repo.is_present(row)
    )


def file_counts(session: Session, title_ids: Sequence[str]) -> dict[str, int]:
    """Count every recorded media file per title, including files not on disk."""

    ids = list(dict.fromkeys(title_ids))
    if not ids:
        return {}
    return _group_counts(
        session,
        select(MediaFile.title_id, func.count(MediaFile.id))
        .where(MediaFile.title_id.in_(ids))
        .group_by(MediaFile.title_id),
    )


def saved_release_counts(session: Session, title_ids: Sequence[str]) -> dict[str, int]:
    ids = list(dict.fromkeys(title_ids))
    if not ids:
        return {}
    return _group_counts(
        session,
        select(Release.title_id, func.count(Release.id))
        .where(Release.title_id.in_(ids), Release.is_saved.is_(True))
        .group_by(Release.title_id),
    )


def _group_counts(session: Session, statement: Any) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in session.execute(statement).all()}


def update_title(session: Session, title_id: str, **fields: Any) -> Title | None:
    allowed = {
        "kind",
        "title",
        "year",
        "imdb_id",
        "tmdb_id",
        "tvdb_id",
        "anilist_id",
        "mal_id",
        "overview",
        "poster_url",
        "banner_url",
        "folder_name",
    }
    row = session.get(Title, title_id)
    if row is None:
        return None
    for key, value in fields.items():
        if key in allowed:
            setattr(row, key, value)
    session.commit()
    session.refresh(row)
    return row


def set_wanted(session: Session, title_id: str, wanted: bool) -> Title | None:
    row = session.get(Title, title_id)
    if row is None:
        return None
    row.wanted = wanted
    row.wanted_at = utc_now_iso() if wanted else None
    session.commit()
    session.refresh(row)
    return row


def delete_title(session: Session, title_id: str) -> bool:
    row = session.get(Title, title_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True
