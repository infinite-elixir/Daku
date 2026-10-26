"""Concurrent release-source aggregation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from daku.services.torrents.base import Release
from daku.services.torrents.nyaa import fetch_releases as fetch_nyaa
from daku.services.torrents.nyaa import matches_episode, matches_query
from daku.services.torrents.torrentio import TorrentioError
from daku.services.torrents.torrentio import fetch_releases as fetch_torrentio


@dataclass(slots=True)
class SourceSearchOutcome:
    releases: list[Release] = field(default_factory=list)
    stale: bool = False
    cached_at: str | None = None
    sources: list[str] = field(default_factory=list)
    sources_degraded: list[str] = field(default_factory=list)


async def search_all_sources(
    client: httpx.AsyncClient,
    cache: Any,
    *,
    kind: str,
    title: str,
    imdb_id: str | None,
    season: int | None,
    episode: int | None,
    pack: bool,
    sources: list[str],
    torrentio_config: str = "",
    synonyms: list[str] | None = None,
    absolute: int | None = None,
) -> SourceSearchOutcome:
    outcome = SourceSearchOutcome()
    tasks: list[tuple[str, asyncio.Task[Any]]] = []
    if "torrentio" in sources and imdb_id:
        media_type = "movie" if kind == "movie" else "series"
        media_id = imdb_id
        if episode is not None and season is not None:
            media_id = f"{imdb_id}:{season}:{episode}"
        elif kind != "movie" and pack and season is not None:
            media_id = f"{imdb_id}:{season}"
        elif kind != "movie" and not pack:
            media_id = ""
        if media_id:
            tasks.append(
                (
                    "torrentio",
                    asyncio.create_task(
                        fetch_torrentio(client, cache, torrentio_config, media_type, media_id)
                    ),
                )
            )
    if kind == "anime" and "nyaa" in sources:
        for query in [title, *(synonyms or [])][:3]:
            if episode is not None:
                query += f" {episode:02d}"
            if absolute is not None:
                query += f" {absolute:02d}"
            tasks.append(("nyaa", asyncio.create_task(fetch_nyaa(client, cache, query))))

    results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
    for (source, _), result in zip(tasks, results, strict=True):
        if isinstance(result, BaseException):
            outcome.sources_degraded.append(source)
            continue
        result = cast(Any, result)
        if source not in outcome.sources:
            outcome.sources.append(source)
        outcome.stale = outcome.stale or result.stale
        outcome.cached_at = outcome.cached_at or result.cached_at
        releases = result.releases
        if source == "nyaa":
            releases = [
                release
                for release in releases
                if matches_query(title, release.filename, synonyms)
                and matches_episode(
                    release.filename,
                    season=season,
                    episode=episode,
                    absolute=absolute,
                    pack=pack,
                )
            ]
        outcome.releases.extend(releases)
    if not outcome.sources and tasks:
        raise TorrentioError("", "all release sources unavailable")
    return outcome
