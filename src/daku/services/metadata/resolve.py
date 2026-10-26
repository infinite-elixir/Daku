"""Hydrate a search candidate into a full :class:`TitleDetail`.

``POST /api/titles`` is the only place that writes titles from metadata. It always
resolves through this module so IMDb, Cinemeta and AniList behave identically.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable

import httpx

from daku.errors import AppError
from daku.services.metadata import jikan
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.cinemeta import _as_year
from daku.services.metadata.models import EpisodeRef, TitleDetail
from daku.services.metadata.search import get_anime_detail, get_detail
from daku.services.metadata.tvdb import TVDBClient

logger = logging.getLogger("daku.metadata.resolve")

IMDB_ID_RE = re.compile(r"^tt\d+$")
IMDB_SOURCES = frozenset({"imdb", "cinemeta", "tvdb"})
ANIME_SOURCES = frozenset({"anilist", "tvdb"})
KINDS = frozenset({"movie", "series", "anime"})
MOVIE_FORMATS = frozenset({"MOVIE"})


def _invalid(message: str, *, detail: dict[str, str] | None = None) -> AppError:
    return AppError(
        code="INVALID_TITLE_SOURCE",
        message=message,
        status_code=422,
        detail=detail or {},
    )


def _anime_episodes(detail: TitleDetail) -> list[EpisodeRef]:
    """AniList does not list episodes, so derive season 1 from the episode count."""

    if detail.episodes:
        return detail.episodes
    if detail.format is not None and detail.format.upper() in MOVIE_FORMATS:
        return []
    count = detail.episode_count or 0
    return [EpisodeRef(season=1, episode=number) for number in range(1, count + 1)]


def _usable_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _target_season(detail: TitleDetail) -> int:
    """Jikan/AniList names describe one entry, so never smear them across seasons."""

    seasons = sorted({ref.season for ref in detail.episodes})
    if len(seasons) == 1:
        return seasons[0]
    return 1 if 1 in seasons else (seasons[0] if seasons else 1)


def _fill_missing(
    detail: TitleDetail,
    *,
    season: int,
    names: dict[int, str | None],
    air_dates: dict[int, str | None] | None = None,
) -> None:
    for ref in detail.episodes:
        if ref.season != season:
            continue
        if _usable_title(ref.title) is None:
            value = _usable_title(names.get(ref.episode))
            if value is not None:
                ref.title = value
        if air_dates is not None and _usable_title(ref.air_date) is None:
            aired = _usable_title(air_dates.get(ref.episode))
            if aired is not None:
                ref.air_date = aired


def _fill_from_refs(detail: TitleDetail, refs: Iterable[EpisodeRef]) -> None:
    by_key = {
        (ref.season, ref.episode): ref
        for ref in refs
        if _usable_title(ref.title) is not None or _usable_title(ref.air_date) is not None
    }
    if not by_key:
        return
    for ref in detail.episodes:
        source = by_key.get((ref.season, ref.episode))
        if source is None:
            continue
        if _usable_title(ref.title) is None:
            value = _usable_title(source.title)
            if value is not None:
                ref.title = value
        if _usable_title(ref.air_date) is None:
            aired = _usable_title(source.air_date)
            if aired is not None:
                ref.air_date = aired


async def enrich_anime_episode_names(
    client: httpx.AsyncClient,
    cache: TTLCache,
    detail: TitleDetail,
    *,
    mal_id: int | None = None,
    tvdb_refs: Iterable[EpisodeRef] | None = None,
    deadline: float | None = None,
    title_id: str | None = None,
) -> dict[str, object]:
    """Fill missing anime episode names, in T-036 precedence order.

    TVDB first (specials and split seasons are already correct there), then Jikan
    by MAL id, then AniList ``streamingEpisodes``. Existing names are never erased.
    """

    if detail.kind != "anime" or not detail.episodes:
        return {"source": None, "found": 0, "missing": 0}

    if tvdb_refs is not None:
        _fill_from_refs(detail, tvdb_refs)

    resolved_mal = mal_id if mal_id is not None else detail.id_mal
    if resolved_mal:
        season = _target_season(detail)
        expected = sum(1 for ref in detail.episodes if ref.season == season)
        episodes = await jikan.fetch_episode_titles(
            client,
            cache,
            resolved_mal,
            episode_count=expected or detail.episode_count,
            deadline=deadline,
        )
        if episodes:
            _fill_missing(
                detail,
                season=season,
                names={episode.number: episode.title for episode in episodes},
                air_dates={episode.number: episode.air_date for episode in episodes},
            )

    if detail.streaming_episodes:
        season = _target_season(detail)
        _fill_missing(
            detail,
            season=season,
            names={
                int(item["episode"]): item.get("title")
                for item in detail.streaming_episodes
                if isinstance(item.get("episode"), int)
            },
        )

    found = sum(1 for ref in detail.episodes if _usable_title(ref.title) is not None)
    missing = len(detail.episodes) - found
    if tvdb_refs is not None:
        source_label = "tvdb"
    elif resolved_mal:
        source_label = "jikan"
    else:
        source_label = "anilist"
    logger.info(
        "anime episode titles enriched",
        extra={
            "title_id": title_id,
            "source": source_label,
            "found": found,
            "missing": missing,
        },
    )
    return {"source": source_label, "found": found, "missing": missing}


async def resolve_candidate_detail(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    source: str,
    source_id: str,
    kind: str,
) -> tuple[TitleDetail, bool]:
    """Return a hydrated title and whether it came from stale cache."""

    source = source.strip().lower()
    source_id = source_id.strip()

    if kind not in KINDS:
        raise _invalid(f"Unsupported kind: {kind}", detail={"kind": kind})

    if kind == "anime" and source != "tvdb":
        if source not in ANIME_SOURCES:
            raise _invalid(
                "Anime titles must be resolved from AniList or TVDB",
                detail={"source": source, "kind": kind},
            )
        try:
            anilist_id = int(source_id)
        except ValueError as exc:
            raise _invalid("AniList ids are numeric", detail={"source_id": source_id}) from exc
        if anilist_id <= 0:
            raise _invalid("AniList ids are positive", detail={"source_id": source_id})
        detail, stale = await get_anime_detail(client, cache, anilist_id)
        detail.episodes = _anime_episodes(detail)
        await enrich_anime_episode_names(
            client,
            cache,
            detail,
            deadline=time.monotonic() + 5.0,
        )
        return detail, stale

    if source == "tvdb":
        # TVDB uses IMDb id to find the series, then returns episodes
        from daku import config

        api_key = config.load_settings().tvdb_api_key
        tvdb = TVDBClient(api_key if api_key else None)
        series = await tvdb.resolve_series(client, source_id, cache)
        if series is None:
            raise AppError(
                code="METADATA_UNAVAILABLE",
                message=f"TVDB series not found for {source_id}",
                status_code=502,
            )
        tvdb_id = series.get("id") or series.get("tvdb_id")
        if tvdb_id is None:
            raise AppError(
                code="METADATA_UNAVAILABLE",
                message=f"TVDB series has no id for {source_id}",
                status_code=502,
            )
        episodes_raw = await tvdb.episodes(client, tvdb_id, cache)
        episodes_refs = []
        for ep in episodes_raw:
            if isinstance(ep, dict):
                episodes_refs.append(
                    EpisodeRef(
                        season=ep.get("seasonNumber") or ep.get("season") or 1,
                        episode=ep.get("number") or ep.get("episode") or 1,
                        title=ep.get("name") or ep.get("title"),
                        air_date=ep.get("aired") or ep.get("airDate"),
                    )
                )
        # For movies, episodes list is empty; for series/series it's populated
        return TitleDetail(
            source="tvdb",
            source_id=source_id,
            kind=kind,
            title=series.get("name") or series.get("title") or source_id,
            year=_as_year(series.get("year")),
            poster_url=series.get("image") or series.get("poster"),
            overview=series.get("overview") or series.get("description"),
            episodes=episodes_refs,
        ), False

    if source not in IMDB_SOURCES:
        raise _invalid(
            "Movies and series must be resolved from IMDb, Cinemeta or TVDB",
            detail={"source": source, "kind": kind},
        )
    if not IMDB_ID_RE.fullmatch(source_id):
        raise _invalid("IMDb ids must look like tt0000000", detail={"source_id": source_id})

    return await get_detail(client, cache, kind, source_id)


async def resolve_candidate(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    source: str,
    source_id: str,
    kind: str,
) -> TitleDetail:
    """Return a fully hydrated title from whichever metadata source it came from."""

    detail, _stale = await resolve_candidate_detail(
        client,
        cache,
        source=source,
        source_id=source_id,
        kind=kind,
    )
    return detail


def fallback_detail(
    *,
    source: str,
    source_id: str,
    kind: str,
    title: str | None = None,
    year: int | None = None,
    poster_url: str | None = None,
) -> TitleDetail:
    """Minimal entry used when metadata is unreachable.

    A library entry is not a download: an add must survive a metadata outage.
    """

    return TitleDetail(
        source=source.strip().lower(),
        source_id=source_id.strip(),
        kind=kind,
        title=(title or "").strip() or source_id.strip(),
        year=year,
        poster_url=poster_url,
    )
