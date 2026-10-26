"""Anime-specific episode resolution on top of the AniBridge mapping (T-005).

The AniList entry is the unit of intent; this module translates it to and from the
season/episode numbering a Jellyfin library understands. Database lookups never raise
when the dataset is missing: callers fall back to IMDb-only behaviour.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import AnimeMapping, Title
from daku.repositories import seasons as seasons_repo
from daku.services.metadata.mapping import (
    EpisodeMapping,
    find_override,
    lookup_anilist_to_imdb,
    lookup_imdb_to_anilist,
    override_mapping,
    resolve_episode,
)


def resolve_title_episode(
    session: Session, title: Title, season: int, episode: int
) -> EpisodeMapping | None:
    """Resolve ``(season, episode)`` for a library anime title.

    A manual override always wins. Dataset resolution requires both an AniList id and
    an IMDb (or TVDB) id; otherwise ``None`` means "use IMDb-only behaviour".
    """

    if title.kind != "anime":
        return None

    override = find_override(session, title.id, season, episode)
    if override is not None:
        return override_mapping(title, override)

    if title.anilist_id is None:
        return None

    # A split-cour season owns its own AniList entry.
    season_anilist_id = seasons_repo.season_anilist_id(session, title.id, season)
    target_anilist_id = season_anilist_id or title.anilist_id

    if title.imdb_id:
        mapped = lookup_imdb_to_anilist(
            session, title.imdb_id, season, episode, anilist_id=target_anilist_id
        )
        if mapped is not None:
            return mapped

    if title.tvdb_id:
        return resolve_episode(
            session,
            source_provider="tvdb_show",
            source_id=title.tvdb_id,
            source_scope=f"s{season}",
            episode=episode,
            target_provider="anilist",
            target_id=str(target_anilist_id),
        )
    return None


def _source_ids(session: Session, anilist_id: int, provider: str) -> list[str]:
    statement = (
        select(AnimeMapping.tgt_id)
        .where(
            AnimeMapping.src_provider == "anilist",
            AnimeMapping.src_id == str(anilist_id),
            AnimeMapping.tgt_provider == provider,
        )
        .distinct()
    )
    return [row for row in session.scalars(statement)]


def episode_map_for_anilist(
    session: Session,
    anilist_id: int,
    *,
    season: int | None,
    episode: int,
) -> EpisodeMapping | None:
    """Map an AniList entry to/from the source numbering used by a library.

    * with ``season``: interpret ``episode`` as an IMDb/TVDB season episode and return
      the AniList episode it corresponds to;
    * without ``season``: interpret ``episode`` as an AniList episode and return the
      IMDb/TVDB season episode that contains it.
    """

    if season is None:
        return lookup_anilist_to_imdb(session, anilist_id, episode)

    for provider in ("imdb_show", "tvdb_show"):
        for source_id in _source_ids(session, anilist_id, provider):
            mapped = resolve_episode(
                session,
                source_provider=provider,
                source_id=source_id,
                source_scope=f"s{season}",
                episode=episode,
                target_provider="anilist",
                target_id=str(anilist_id),
            )
            if mapped is not None:
                return mapped
    return None


__all__ = ["episode_map_for_anilist", "resolve_title_episode"]
