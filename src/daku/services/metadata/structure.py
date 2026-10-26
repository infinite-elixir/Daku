"""Season/episode structure from aggregate providers (T-037).

AniList models one entry per cour; IMDb/Cinemeta and TVDB model the whole series
(Season 00 specials, Season 01, Season 02, ...). The library follows the aggregate
structure while the title keeps its AniList identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.mapping import anilist_id_for_season
from daku.services.metadata.models import EpisodeRef
from daku.services.metadata.resolve import _anime_episodes, enrich_anime_episode_names
from daku.services.metadata.search import get_anime_detail, get_detail
from daku.services.metadata.tvdb import TVDBClient

STRUCTURE_SOURCES = frozenset({"cinemeta", "tvdb", "anilist"})
_PROVIDER_FOR_SOURCE = {"cinemeta": "imdb_show", "tvdb": "tvdb_show"}


@dataclass(slots=True)
class SeasonSpec:
    library_season: int
    provider_season: int
    source: str
    source_id: str
    anilist_id: int | None
    absolute_offset: int
    episode_count: int


@dataclass(slots=True)
class Structure:
    source: str
    source_id: str
    episodes: list[EpisodeRef] = field(default_factory=list)


def tvdb_refs(raw: list[dict[str, Any]]) -> list[EpisodeRef]:
    refs: list[EpisodeRef] = []
    for episode in raw:
        if not isinstance(episode, dict):
            continue
        refs.append(
            EpisodeRef(
                season=int(episode.get("seasonNumber") or episode.get("season") or 1),
                episode=int(episode.get("number") or episode.get("episode") or 1),
                title=episode.get("name") or episode.get("title"),
                air_date=episode.get("aired") or episode.get("airDate"),
            )
        )
    return refs


async def tvdb_structure(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    imdb_id: str | None,
    tvdb_id: str | None,
    deadline: float | None,
) -> Structure | None:
    from daku import config

    api_key = config.load_settings().tvdb_api_key
    tvdb = TVDBClient(api_key if api_key else None)
    if not tvdb.configured:
        return None

    resolved = tvdb_id
    if not resolved and imdb_id:
        series = await tvdb.resolve_series(client, imdb_id, cache)
        if series is not None:
            found = series.get("id") or series.get("tvdb_id")
            resolved = None if found is None else str(found)
    if not resolved:
        return None
    raw = await tvdb.episodes(client, resolved, cache)
    refs = tvdb_refs(raw)
    if not refs:
        return None
    return Structure(source="tvdb", source_id=str(resolved), episodes=refs)


async def hydrate_structure(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    kind: str,
    imdb_id: str | None = None,
    tvdb_id: str | None = None,
    anilist_id: int | None = None,
    detail: Any = None,
    prefer: str = "cinemeta",
    deadline: float | None = None,
) -> Structure:
    """Return the aggregate structure, falling back without raising."""

    if kind == "movie":
        return Structure(source=prefer, source_id=imdb_id or tvdb_id or "", episodes=[])

    if prefer in {"cinemeta", "auto"} and imdb_id:
        try:
            cinemeta_detail, _stale = await get_detail(client, cache, "series", imdb_id)
        except Exception:
            cinemeta_detail = None
        if cinemeta_detail is not None and cinemeta_detail.episodes:
            return Structure(
                source="cinemeta",
                source_id=imdb_id,
                episodes=list(cinemeta_detail.episodes),
            )

    if prefer in {"tvdb", "auto"} and (tvdb_id or imdb_id):
        try:
            tvdb_result = await tvdb_structure(
                client, cache, imdb_id=imdb_id, tvdb_id=tvdb_id, deadline=deadline
            )
        except Exception:
            tvdb_result = None
        if tvdb_result is not None:
            return tvdb_result

    if anilist_id:
        try:
            anime_detail, _stale = await get_anime_detail(client, cache, anilist_id)
        except Exception:
            anime_detail = None
        if anime_detail is not None:
            episodes = _anime_episodes(anime_detail)
            if episodes:
                await enrich_anime_episode_names(client, cache, anime_detail, deadline=deadline)
                return Structure(
                    source="anilist",
                    source_id=str(anilist_id),
                    episodes=list(anime_detail.episodes),
                )

    if detail is not None and getattr(detail, "episodes", None):
        return Structure(
            source=detail.source, source_id=detail.source_id, episodes=list(detail.episodes)
        )

    return Structure(source=prefer, source_id=imdb_id or tvdb_id or "", episodes=[])


def season_specs(
    session: Any,
    *,
    episodes: list[EpisodeRef],
    source: str,
    source_id: str,
    default_anilist_id: int | None = None,
    offsets: dict[int, int] | None = None,
) -> list[SeasonSpec]:
    """Group episodes into library seasons and compute cumulative offsets."""

    provider_seasons = sorted({ref.season for ref in episodes})
    if not provider_seasons:
        return []

    resolved = offsets or {}
    library_for_provider = {
        provider: resolved.get(provider, provider) for provider in provider_seasons
    }
    counts: dict[int, int] = {}
    for ref in episodes:
        library = library_for_provider[ref.season]
        counts[library] = counts.get(library, 0) + 1

    running = 0
    cumulative: dict[int, int] = {}
    for library in sorted(counts):
        if library == 0:
            cumulative[library] = 0
            continue
        cumulative[library] = running
        running += counts[library]

    provider_for_source = _PROVIDER_FOR_SOURCE.get(source)
    specs: list[SeasonSpec] = []
    for provider in provider_seasons:
        library = library_for_provider[provider]
        anilist_id = None
        if library != 0:
            if provider_for_source:
                anilist_id = anilist_id_for_season(
                    session,
                    source_provider=provider_for_source,
                    source_id=source_id,
                    season=provider,
                )
            if anilist_id is None and provider == 1 and library == 1:
                anilist_id = default_anilist_id
        specs.append(
            SeasonSpec(
                library_season=library,
                provider_season=provider,
                source=source,
                source_id=source_id,
                anilist_id=anilist_id,
                absolute_offset=cumulative.get(library, 0),
                episode_count=counts.get(library, 0),
            )
        )
    specs.sort(key=lambda spec: spec.library_season)
    return specs


def library_refs(episodes: list[EpisodeRef], specs: list[SeasonSpec]) -> list[EpisodeRef]:
    mapping = {spec.provider_season: spec.library_season for spec in specs}
    moved = [replace(ref, season=mapping.get(ref.season, ref.season)) for ref in episodes]
    return sorted(moved, key=lambda ref: (ref.season, ref.episode))


def absolute_offsets(specs: list[SeasonSpec]) -> dict[int, int]:
    return {spec.library_season: spec.absolute_offset for spec in specs if spec.library_season != 0}


def specs_for_rows(rows: list[Any], *, episode_counts: dict[int, int]) -> list[dict[str, Any]]:
    return [
        {
            "library_season": row.library_season,
            "provider_season": row.provider_season,
            "source": row.source,
            "source_id": row.source_id,
            "anilist_id": row.anilist_id,
            "absolute_offset": row.absolute_offset,
            "episode_count": episode_counts.get(row.library_season, 0),
        }
        for row in rows
    ]


__all__ = [
    "SeasonSpec",
    "Structure",
    "absolute_offsets",
    "hydrate_structure",
    "library_refs",
    "season_specs",
    "specs_for_rows",
    "tvdb_refs",
]
