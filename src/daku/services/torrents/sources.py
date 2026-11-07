"""Concurrent release-source aggregation through the source registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from daku.services.metadata.aliases import dedupe_aliases, derive_styles
from daku.services.torrents.animetosho import (
    MAX_CANDIDATES,
    MAX_QUERIES,
    SEARCH_DEADLINE_SECONDS,
    AnimeToshoSearch,
    build_bare_queries,
    build_query_plan,
)
from daku.services.torrents.animetosho import fetch_releases as fetch_animetosho
from daku.services.torrents.base import KeywordQuery, Release
from daku.services.torrents.matching import (
    matches_episode,
    matches_query,
    pack_covers_episode,
    pack_label,
)
from daku.services.torrents.registry import is_known_source
from daku.services.torrents.torrentio import TorrentioError
from daku.services.torrents.torrentio import fetch_releases as fetch_torrentio
from daku.services.torrents.torrentscsv import TorrentsCsvSearch
from daku.services.torrents.torrentscsv import fetch_releases as fetch_torrentscsv

# The per-process throttle serialises the actual HTTP call, so a small task window
# only overlaps waiting, never the request itself.
_QUERY_WINDOW = 4


@dataclass(slots=True)
class SourceSearchOutcome:
    releases: list[Release] = field(default_factory=list)
    stale: bool = False
    cached_at: str | None = None
    sources: list[str] = field(default_factory=list)
    sources_degraded: list[str] = field(default_factory=list)
    queries: int = 0
    aliases: int = 0
    packs: list[Release] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)


def _matches(
    filenames: list[str],
    *,
    aliases: list[str],
    season: int | None,
    episode: int | None,
    absolute: int | None,
    pack: bool,
    provider_seasons: list[int] | None,
    season_fallback: bool,
) -> list[str]:
    matched: list[str] = []
    for filename in filenames:
        if not matches_query(aliases[0], filename, aliases[1:]):
            continue
        if not matches_episode(
            filename,
            season=season,
            episode=episode,
            absolute=absolute,
            pack=pack,
            provider_seasons=provider_seasons,
            season_fallback=season_fallback,
        ):
            continue
        matched.append(filename)
    return matched


async def _run_queries(
    client: httpx.AsyncClient,
    cache: Any,
    queries: list[str],
    *,
    aliases: list[str],
    season: int | None,
    episode: int | None,
    absolute: int | None,
    pack: bool,
    provider_seasons: list[int] | None,
    season_fallback: bool,
    deadline: float,
    collected: int,
    budget: int,
) -> tuple[list[AnimeToshoSearch], int, int, int]:
    """Run a bounded batch, stopping when the candidate/deadline cap is reached."""

    if not queries or budget <= 0 or collected >= MAX_CANDIDATES:
        return [], 0, 0, collected

    iterator = iter(queries[:budget])
    running: dict[asyncio.Task[AnimeToshoSearch], str] = {}
    results: list[AnimeToshoSearch] = []
    issued = 0
    degraded = 0
    loop = asyncio.get_running_loop()

    def spawn() -> None:
        nonlocal issued
        while len(running) < _QUERY_WINDOW:
            try:
                query = next(iterator)
            except StopIteration:
                return
            running[asyncio.create_task(fetch_animetosho(client, cache, query))] = query
            issued += 1

    spawn()
    try:
        while running:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, _ = await asyncio.wait(
                running, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                break
            for task in done:
                running.pop(task, None)
                try:
                    result = task.result()
                except Exception:
                    degraded += 1
                    continue
                results.append(result)
                collected += len(
                    _matches(
                        [release.filename for release in result.releases],
                        aliases=aliases,
                        season=season,
                        episode=episode,
                        absolute=absolute,
                        pack=pack,
                        provider_seasons=provider_seasons,
                        season_fallback=season_fallback,
                    )
                )
            if collected >= MAX_CANDIDATES:
                break
            spawn()
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    return results, issued, degraded, collected


async def _search_animetosho(
    client: httpx.AsyncClient,
    cache: Any,
    outcome: SourceSearchOutcome,
    *,
    title: str,
    synonyms: list[str] | None,
    season: int | None,
    episode: int | None,
    absolute: int | None,
    pack: bool,
    provider_seasons: list[int] | None,
    season_fallback: bool,
) -> None:
    aliases = dedupe_aliases([title, *(synonyms or [])])
    match_aliases = dedupe_aliases(
        [*aliases, *(style for alias in aliases for style in derive_styles(alias))]
    )
    outcome.aliases = len(aliases)
    plan = build_query_plan(
        aliases,
        season=season,
        episode=episode,
        provider_seasons=provider_seasons,
    )
    bare_queries = set(build_bare_queries(aliases))
    bare = [query for query in plan if query in bare_queries]
    suffix = [query for query in plan if query not in bare_queries]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + SEARCH_DEADLINE_SECONDS
    candidates = 0
    degraded = 0
    results, issued, bare_degraded, candidates = await _run_queries(
        client,
        cache,
        bare,
        aliases=match_aliases,
        season=season,
        episode=episode,
        absolute=absolute,
        pack=pack,
        provider_seasons=provider_seasons,
        season_fallback=season_fallback,
        deadline=deadline,
        collected=candidates,
        budget=MAX_QUERIES,
    )
    degraded += bare_degraded
    # The common case is one request per alias: only spend the remaining
    # budget on episode spellings when the bare names under-delivered.
    if (
        episode is not None
        and suffix
        and candidates < 20
        and candidates < MAX_CANDIDATES
        and loop.time() < deadline
    ):
        extra, extra_issued, extra_degraded, candidates = await _run_queries(
            client,
            cache,
            suffix,
            aliases=match_aliases,
            season=season,
            episode=episode,
            absolute=absolute,
            pack=pack,
            provider_seasons=provider_seasons,
            season_fallback=season_fallback,
            deadline=deadline,
            collected=candidates,
            budget=MAX_QUERIES - issued,
        )
        results.extend(extra)
        issued += extra_issued
        degraded += extra_degraded

    outcome.queries = issued
    if results:
        outcome.sources.append("animetosho")
        seen_packs: set[str] = set()
        for result in results:
            outcome.stale = outcome.stale or result.stale
            outcome.cached_at = outcome.cached_at or result.cached_at
            for release in result.releases:
                if not matches_query(match_aliases[0], release.filename, match_aliases[1:]):
                    continue
                me = matches_episode(
                    release.filename,
                    season=season,
                    episode=episode,
                    absolute=absolute,
                    pack=pack,
                    provider_seasons=provider_seasons,
                    season_fallback=season_fallback,
                )
                if me:
                    outcome.releases.append(release)
                    continue
                # A pack that contains the requested episode is a valid source
                # (the download selects one file), but it is not an episode
                # match and is labelled as a pack.
                pce = pack_covers_episode(
                    release.filename,
                    season=season,
                    episode=episode,
                    provider_seasons=provider_seasons,
                    season_fallback=season_fallback,
                )
                if episode is not None and not pack and release.info_hash not in seen_packs and pce:
                    seen_packs.add(release.info_hash)
                    outcome.packs.append(
                        replace(
                            release,
                            is_pack=True,
                            pack_label=pack_label(release.filename),
                        )
                    )
        outcome.per_source["animetosho"] = len(outcome.releases) + len(outcome.packs)
    if degraded and "animetosho" not in outcome.sources_degraded:
        outcome.sources_degraded.append("animetosho")


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
    provider_seasons: list[int] | None = None,
    season_fallback: bool = False,
    year: int | None = None,
) -> SourceSearchOutcome:
    outcome = SourceSearchOutcome()
    selected = [name for name in dict.fromkeys(sources) if is_known_source(name, kind)]
    keyword = KeywordQuery(
        title=title,
        year=year,
        season=season,
        episode=episode,
        pack=pack,
        aliases=tuple(synonyms or ()),
    )

    torrentio_task: asyncio.Task[Any] | None = None
    if "torrentio" in selected and imdb_id:
        media_type = "movie" if kind == "movie" else "series"
        media_id = imdb_id
        if episode is not None and season is not None:
            media_id = f"{imdb_id}:{season}:{episode}"
        elif kind != "movie" and pack and season is not None:
            media_id = f"{imdb_id}:{season}"
        elif kind != "movie" and not pack:
            media_id = ""
        if media_id:
            torrentio_task = asyncio.create_task(
                fetch_torrentio(client, cache, torrentio_config, media_type, media_id)
            )

    csv_task: asyncio.Task[TorrentsCsvSearch] | None = None
    if "torrents-csv" in selected:
        csv_task = asyncio.create_task(fetch_torrentscsv(client, cache, keyword))

    if "animetosho" in selected and kind == "anime":
        await _search_animetosho(
            client,
            cache,
            outcome,
            title=title,
            synonyms=synonyms,
            season=season,
            episode=episode,
            absolute=absolute,
            pack=pack,
            provider_seasons=provider_seasons,
            season_fallback=season_fallback,
        )

    if torrentio_task is not None:
        try:
            result = await torrentio_task
        except Exception:
            outcome.sources_degraded.append("torrentio")
        else:
            outcome.sources.insert(0, "torrentio")
            outcome.stale = outcome.stale or result.stale
            outcome.cached_at = outcome.cached_at or result.cached_at
            outcome.releases.extend(result.releases)
            outcome.per_source["torrentio"] = len(result.releases)

    if csv_task is not None:
        try:
            csv_result = await csv_task
        except Exception:
            outcome.sources_degraded.append("torrents-csv")
        else:
            outcome.sources.append("torrents-csv")
            outcome.stale = outcome.stale or csv_result.stale
            outcome.cached_at = outcome.cached_at or csv_result.cached_at
            outcome.releases.extend(csv_result.releases)
            outcome.queries += csv_result.queries
            outcome.per_source["torrents-csv"] = len(csv_result.releases)

    outcome.sources_degraded = list(dict.fromkeys(outcome.sources_degraded))
    searched = torrentio_task is not None or bool({"animetosho", "torrents-csv"} & set(selected))
    if not outcome.sources and searched:
        raise TorrentioError("", "all release sources unavailable")
    return outcome
