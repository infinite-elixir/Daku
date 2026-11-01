"""Manual metadata overrides: identity, season offsets and episode renumbering (T-029).

The database is the source of truth for what the user decided. ``titles.*`` is kept in
sync so existing readers keep working, while the override rows stop a later metadata
refresh from undoing the decision.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daku.errors import AppError
from daku.models import (
    Episode,
    MediaFile,
    Title,
    TitleEpisodeOverride,
    TitleOverride,
    TitleSeasonOffset,
)
from daku.services.metadata.models import EpisodeRef
from daku.utils import new_id, utc_now_iso

logger = logging.getLogger("daku.metadata.overrides")

EDITABLE_FIELDS = (
    "kind",
    "title",
    "year",
    "imdb_id",
    "tvdb_id",
    "anilist_id",
    "metadata_source",
    "aliases",
)
KINDS = frozenset({"movie", "series", "anime"})
METADATA_SOURCES = frozenset({"cinemeta", "tvdb", "anilist"})
_IMDB_RE = re.compile(r"^tt\d+$")
_NUMERIC_RE = re.compile(r"^\d+$")


def _invalid(message: str, *, detail: dict[str, Any] | None = None) -> AppError:
    return AppError(
        code="INVALID_IDENTITY",
        message=message,
        status_code=422,
        detail=detail or {},
    )


# --------------------------------------------------------------------------- read


def load_field_overrides(session: Session, title_id: str) -> dict[str, Any]:
    rows = session.scalars(select(TitleOverride).where(TitleOverride.title_id == title_id)).all()
    values: dict[str, Any] = {}
    for row in rows:
        try:
            values[row.field] = json.loads(row.value_json)
        except (TypeError, json.JSONDecodeError):
            continue
    return values


def load_season_offsets(session: Session, title_id: str) -> dict[int, int]:
    rows = session.scalars(
        select(TitleSeasonOffset).where(TitleSeasonOffset.title_id == title_id)
    ).all()
    return {row.provider_season: row.library_season for row in rows}


def load_episode_overrides(session: Session, title_id: str) -> list[TitleEpisodeOverride]:
    return list(
        session.scalars(
            select(TitleEpisodeOverride)
            .where(TitleEpisodeOverride.title_id == title_id)
            .order_by(
                TitleEpisodeOverride.season,
                TitleEpisodeOverride.episode,
                TitleEpisodeOverride.target_season,
                TitleEpisodeOverride.target_episode,
            )
        )
    )


def overrides_bundle(session: Session, title: Title) -> dict[str, Any]:
    fields = load_field_overrides(session, title.id)
    offsets = load_season_offsets(session, title.id)
    episodes = load_episode_overrides(session, title.id)
    from daku.services.metadata.aliases import generated_aliases

    provider = {
        field: (
            generated_aliases(title)
            if field == "aliases"
            else (None if field in fields else getattr(title, field, None))
        )
        for field in EDITABLE_FIELDS
    }
    return {
        "title_id": title.id,
        "fields": fields,
        "season_offsets": [
            {"provider_season": provider_season, "library_season": library_season}
            for provider_season, library_season in sorted(offsets.items())
        ],
        "episodes": [
            {
                "id": row.id,
                "season": row.season,
                "episode": row.episode,
                "target_season": row.target_season,
                "target_episode": row.target_episode,
                "absolute": row.absolute,
                "created_at": row.created_at,
            }
            for row in episodes
        ],
        "provider_values": provider,
    }


# --------------------------------------------------------------------------- helpers


def _normalise_identity(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "kind":
        kind = str(value).strip().lower()
        if kind not in KINDS:
            raise _invalid("kind must be movie, series or anime", detail={"kind": value})
        return kind
    if field == "title":
        title = str(value).strip()
        if not title:
            raise _invalid("title must not be empty")
        return title
    if field == "year":
        if isinstance(value, bool):
            raise _invalid("year must be a number")
        try:
            year = int(value)
        except (TypeError, ValueError) as exc:
            raise _invalid("year must be a number", detail={"year": value}) from exc
        if year < 1800 or year > 2200:
            raise _invalid("year is outside the supported range", detail={"year": year})
        return year
    if field == "metadata_source":
        source = str(value).strip().lower()
        if source not in METADATA_SOURCES:
            raise _invalid(
                "metadata_source must be cinemeta, tvdb or anilist",
                detail={"metadata_source": value},
            )
        return source
    if field == "aliases":
        if not isinstance(value, (list, tuple)):
            raise _invalid("aliases must be a list of strings", detail={"aliases": value})
        from daku.services.metadata.aliases import dedupe_aliases

        aliases = dedupe_aliases(str(item) for item in value if isinstance(item, str))
        if len(aliases) > 50:
            raise _invalid("at most 50 aliases may be stored", detail={"aliases": len(aliases)})
        return aliases
    text = str(value).strip()
    if not text:
        return None
    if field == "imdb_id" and not _IMDB_RE.fullmatch(text):
        raise _invalid("IMDb ids look like tt0000000", detail={"imdb_id": text})
    if field in {"tvdb_id", "anilist_id"} and not _NUMERIC_RE.fullmatch(text):
        raise _invalid(f"{field} must be numeric", detail={field: text})
    return text


def _conflict(session: Session, *, field: str, value: Any, title_id: str) -> None:
    if value is None:
        return
    column = getattr(Title, field)
    row = session.scalar(select(Title).where(column == value, Title.id != title_id).limit(1))
    if row is not None:
        raise AppError(
            code="IDENTITY_CONFLICT",
            message=f"{field} {value} already belongs to {row.title}",
            status_code=409,
            detail={"title_id": row.id, "title": row.title, "field": field, "value": value},
        )


def _validate_identity(kind: str, values: dict[str, Any]) -> None:
    if kind in {"movie", "series"} and not (values.get("imdb_id") or values.get("tvdb_id")):
        raise _invalid(
            f"{kind} titles need an IMDb or TVDB id",
            detail={"kind": kind},
        )
    if kind == "anime" and not (values.get("anilist_id") or values.get("tvdb_id")):
        raise _invalid(
            "anime titles need an AniList id (or a TVDB id fallback)",
            detail={"kind": kind},
        )


def _has_structural_children(session: Session, title_id: str) -> dict[str, int]:
    episodes = int(
        session.scalar(select(func.count(Episode.id)).where(Episode.title_id == title_id)) or 0
    )
    files = int(
        session.scalar(select(func.count(MediaFile.id)).where(MediaFile.title_id == title_id)) or 0
    )
    return {"episodes": episodes, "media_files": files}


def _write_field_override(session: Session, title_id: str, field: str, value: Any) -> None:
    row = session.get(TitleOverride, {"title_id": title_id, "field": field})
    if row is None:
        row = TitleOverride(title_id=title_id, field=field)
        session.add(row)
    row.value_json = json.dumps(value, ensure_ascii=False)
    row.updated_at = utc_now_iso()


# --------------------------------------------------------------------------- identity


def update_identity(
    session: Session,
    title: Title,
    changes: dict[str, Any],
    *,
    force: bool = False,
) -> tuple[Title, bool]:
    """Apply, validate and persist identity changes. Returns ``(title, needs_refresh)``."""

    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise _invalid(f"fields are not editable: {', '.join(sorted(unknown))}")

    normalised: dict[str, Any] = {}
    for field, value in changes.items():
        value = _normalise_identity(field, value)
        if field == "anilist_id" and value is not None:
            value = int(value)
        normalised[field] = value

    values = {field: getattr(title, field, None) for field in EDITABLE_FIELDS}
    values.update(normalised)
    kind = values["kind"] or title.kind
    values["kind"] = kind
    _validate_identity(kind, values)
    if kind == "anime":
        # AniList is the identity for anime; keep it ahead of any old Cinemeta source.
        normalised.setdefault("metadata_source", "anilist")
        values["metadata_source"] = normalised["metadata_source"]

    _conflict(session, field="imdb_id", value=values.get("imdb_id"), title_id=title.id)
    _conflict(session, field="anilist_id", value=values.get("anilist_id"), title_id=title.id)

    kind_changed = kind != title.kind
    needs_refresh = kind_changed and kind != "movie"
    if kind_changed and kind == "movie":
        children = _has_structural_children(session, title.id)
        if (children["episodes"] or children["media_files"]) and not force:
            raise AppError(
                code="KIND_HAS_EPISODES",
                message=(
                    "This title still has episodes or media files; confirm the conversion "
                    "to remove only the episode rows"
                ),
                status_code=409,
                detail=children,
            )
        if force and children["episodes"]:
            # Keep media rows (and the files on disk); only the episode grouping goes.
            session.execute(
                update(MediaFile)
                .where(MediaFile.title_id == title.id, MediaFile.episode_id.is_not(None))
                .values(episode_id=None)
            )
            session.execute(delete(Episode).where(Episode.title_id == title.id))

    for field, value in normalised.items():
        if field != "aliases":
            setattr(title, field, value)
    for field in normalised:
        value = normalised[field] if field == "aliases" else getattr(title, field, None)
        _write_field_override(session, title.id, field, value)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(
            code="IDENTITY_CONFLICT",
            message="Another title already uses that identity",
            status_code=409,
        ) from exc
    session.refresh(title)
    return title, needs_refresh


def delete_field_override(session: Session, title_id: str, field: str) -> bool:
    if field not in EDITABLE_FIELDS:
        raise AppError(
            code="OVERRIDE_NOT_FOUND",
            message=f"{field} is not an overridable field",
            status_code=404,
        )
    row = session.get(TitleOverride, {"title_id": title_id, "field": field})
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


# --------------------------------------------------------------------------- episodes


def _find_row(session: Session, title_id: str, season: int, episode: int) -> Episode | None:
    return session.scalar(
        select(Episode)
        .where(
            Episode.title_id == title_id,
            Episode.season == season,
            Episode.episode == episode,
        )
        .limit(1)
    )


def revert_episode_overrides(
    session: Session,
    title_id: str,
    rows: list[TitleEpisodeOverride] | None = None,
) -> None:
    """Move overridden episode rows back to their provider coordinates (no commit)."""

    for override in rows if rows is not None else load_episode_overrides(session, title_id):
        row = _find_row(session, title_id, override.target_season, override.target_episode)
        if row is None:
            continue
        if (row.season, row.episode) == (override.season, override.episode):
            continue
        occupied = _find_row(session, title_id, override.season, override.episode)
        if occupied is not None and occupied.id != row.id:
            logger.warning(
                "episode override revert skipped: target occupied",
                extra={
                    "title_id": title_id,
                    "season": override.season,
                    "episode": override.episode,
                },
            )
            continue
        row.season = override.season
        row.episode = override.episode
        row.absolute = None


def apply_episode_overrides(session: Session, title_id: str) -> None:
    """Move source episode rows to their override targets (no revert)."""

    for override in load_episode_overrides(session, title_id):
        row = _find_row(session, title_id, override.season, override.episode)
        if row is None:
            continue
        occupied = _find_row(session, title_id, override.target_season, override.target_episode)
        if occupied is not None and occupied.id != row.id:
            logger.warning(
                "episode override skipped: target occupied",
                extra={
                    "title_id": title_id,
                    "target_season": override.target_season,
                    "target_episode": override.target_episode,
                },
            )
            continue
        row.season = override.target_season
        row.episode = override.target_episode
        if override.absolute is not None:
            row.absolute = override.absolute


def apply_season_offsets_to_refs(
    refs: list[EpisodeRef], offsets: dict[int, int]
) -> list[EpisodeRef]:
    if not offsets:
        return refs
    return [replace(ref, season=offsets.get(ref.season, ref.season)) for ref in refs]


def _move_season(
    session: Session,
    title_id: str,
    source_season: int,
    target_season: int,
    *,
    only_episodes: set[int] | None = None,
) -> None:
    if source_season == target_season:
        return
    rows = session.scalars(
        select(Episode).where(Episode.title_id == title_id, Episode.season == source_season)
    ).all()
    for row in rows:
        if only_episodes is not None and row.episode not in only_episodes:
            continue
        occupied = _find_row(session, title_id, target_season, row.episode)
        if occupied is not None and occupied.id != row.id:
            logger.warning(
                "season offset skipped: target occupied",
                extra={
                    "title_id": title_id,
                    "source_season": source_season,
                    "target_season": target_season,
                    "episode": row.episode,
                },
            )
            continue
        row.season = target_season


def set_episode_map(
    session: Session,
    title: Title,
    *,
    offsets: list[dict[str, int]],
    episodes: list[dict[str, Any]],
) -> None:
    """Replace the override set atomically and apply it to existing episode rows."""

    seen_offsets: set[int] = set()
    normalised_offsets: list[tuple[int, int]] = []
    for item in offsets:
        provider_season = int(item["provider_season"])
        library_season = int(item["library_season"])
        if provider_season < 0 or library_season < 0:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message="Season numbers must be zero or greater",
                status_code=422,
            )
        if provider_season in seen_offsets:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message=f"provider season {provider_season} is offset twice",
                status_code=422,
            )
        seen_offsets.add(provider_season)
        normalised_offsets.append((provider_season, library_season))

    normalised_episodes: list[dict[str, Any]] = []
    seen_sources: set[tuple[int, int]] = set()
    seen_targets: set[tuple[int, int]] = set()
    for item in episodes:
        season = int(item["season"])
        episode = int(item["episode"])
        target_season = int(item["target_season"])
        target_episode = int(item["target_episode"])
        absolute = item.get("absolute")
        absolute = None if absolute is None else int(absolute)
        if season < 0 or target_season < 0 or episode < 1 or target_episode < 1:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message="Episode numbers must be positive",
                status_code=422,
            )
        if absolute is not None and absolute < 1:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message="Absolute numbers must be positive",
                status_code=422,
            )
        if (season, episode) in seen_sources:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message=f"S{season:02d}E{episode:02d} has more than one override",
                status_code=422,
            )
        if (target_season, target_episode) in seen_targets:
            raise AppError(
                code="INVALID_EPISODE_MAP",
                message=(f"S{target_season:02d}E{target_episode:02d} is targeted more than once"),
                status_code=422,
            )
        seen_sources.add((season, episode))
        seen_targets.add((target_season, target_episode))
        normalised_episodes.append(
            {
                "season": season,
                "episode": episode,
                "target_season": target_season,
                "target_episode": target_episode,
                "absolute": absolute,
            }
        )

    old_offsets = load_season_offsets(session, title.id)
    old_episodes = load_episode_overrides(session, title.id)
    revert_episode_overrides(session, title.id, old_episodes)

    # Season offsets are an explicit renumber: move existing rows now.
    for provider_season, library_season in old_offsets.items():
        if provider_season not in seen_offsets:
            _move_season(session, title.id, library_season, provider_season)
    for provider_season, library_season in normalised_offsets:
        _move_season(session, title.id, provider_season, library_season)

    session.execute(delete(TitleEpisodeOverride).where(TitleEpisodeOverride.title_id == title.id))
    session.execute(delete(TitleSeasonOffset).where(TitleSeasonOffset.title_id == title.id))
    now = utc_now_iso()
    for provider_season, library_season in normalised_offsets:
        session.add(
            TitleSeasonOffset(
                title_id=title.id,
                provider_season=provider_season,
                library_season=library_season,
                updated_at=now,
            )
        )
    for item in normalised_episodes:
        session.add(
            TitleEpisodeOverride(
                id=new_id(),
                title_id=title.id,
                created_at=now,
                **item,
            )
        )

    session.flush()
    apply_episode_overrides(session, title.id)
    session.commit()


def apply_overrides_to_detail_fields(
    session: Session,
    title: Title,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Drop provider values for fields the user has overridden."""

    overrides = load_field_overrides(session, title.id)
    return {field: value for field, value in updates.items() if field not in overrides}


def overridden_fields(session: Session, title_id: str) -> list[str]:
    return sorted(load_field_overrides(session, title_id))


__all__ = (
    "EDITABLE_FIELDS",
    "apply_episode_overrides",
    "apply_overrides_to_detail_fields",
    "apply_season_offsets_to_refs",
    "delete_field_override",
    "load_episode_overrides",
    "load_field_overrides",
    "load_season_offsets",
    "overridden_fields",
    "overrides_bundle",
    "revert_episode_overrides",
    "set_episode_map",
    "update_identity",
)
