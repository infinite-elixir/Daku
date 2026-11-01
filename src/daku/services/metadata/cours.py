"""Resolve split-cour AniList entries into one library season (T-052).

AniList models each cour as its own entry. IMDb/TVDB and release groups model the
whole run. Daku keeps the aggregate title but needs to know which AniList entry owns
each episode range, and which provider season release names use.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import AnimeMapping
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.models import EpisodeRef, TitleDetail
from daku.services.metadata.resolve import resolve_candidate_detail

ANIME_FORMATS = frozenset({"TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT"})
MAX_COURS = 4
_STOPWORDS = frozenset(
    {
        "a",
        "adventure",
        "an",
        "and",
        "bizarre",
        "bouken",
        "jojo",
        "jojos",
        "kimyou",
        "na",
        "no",
        "of",
        "part",
        "the",
    }
)
_NEXT_SEASON_RE = re.compile(r"\b(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season)\b", re.IGNORECASE)


@dataclass(slots=True)
class CourEntry:
    anilist_id: int
    title: str
    year: int | None
    format: str | None
    episode_count: int
    poster_url: str | None
    banner_url: str | None
    detail: TitleDetail | None = None


@dataclass(slots=True)
class CourRange:
    library_season: int
    provider_season: int
    start_episode: int
    end_episode: int
    anilist_id: int
    absolute_offset: int
    entry: CourEntry


def significant_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _entry_from_relation(node: dict[str, Any]) -> CourEntry | None:
    raw_id = node.get("source_id")
    if not isinstance(raw_id, (int, str)):
        return None
    try:
        anilist_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    title = str(node.get("title") or "").strip()
    if not title:
        return None
    count = node.get("episode_count")
    if not isinstance(count, int) or count <= 0:
        return None
    return CourEntry(
        anilist_id=anilist_id,
        title=title,
        year=node.get("year") if isinstance(node.get("year"), int) else None,
        format=node.get("format"),
        episode_count=count,
        poster_url=node.get("poster_url"),
        banner_url=node.get("banner_url"),
    )


def _is_split_cour(primary: TitleDetail, node: dict[str, Any], entry: CourEntry) -> bool:
    """Conservative test for "same run, next cour" rather than "next season"."""

    node_type = str(node.get("type") or "ANIME").upper()
    if node_type != "ANIME":
        return False
    if (entry.format or "").upper() not in ANIME_FORMATS:
        return False
    if _NEXT_SEASON_RE.search(entry.title):
        return False
    if primary.year is None or entry.year is None or primary.year != entry.year:
        return False
    shared = significant_tokens(primary.title) & significant_tokens(entry.title)
    return len(shared) >= 2


async def _load_entry(
    client: httpx.AsyncClient,
    cache: TTLCache,
    entry: CourEntry,
    deadline: float,
) -> CourEntry:
    """Fill a relation stub from AniList, keeping the stub when it fails."""

    try:
        detail, _stale = await resolve_candidate_detail(
            client,
            cache,
            source="anilist",
            source_id=str(entry.anilist_id),
            kind="anime",
        )
    except Exception:
        return entry
    if detail is None:
        return entry
    entry.detail = detail
    entry.title = detail.title or entry.title
    entry.year = detail.year if detail.year is not None else entry.year
    entry.format = detail.format or entry.format
    entry.poster_url = detail.poster_url or entry.poster_url
    entry.banner_url = detail.banner_url or entry.banner_url
    entry.episode_count = len(detail.episodes) or entry.episode_count
    return entry


async def resolve_cours(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    primary_id: int,
    primary: TitleDetail,
    deadline: float | None = None,
) -> list[CourEntry]:
    """Return the split cours around the primary, ordered oldest first.

    AniList stores each cour as a separate entry linked with ``PREQUEL``/``SEQUEL``.
    Both directions matter: refreshing the second cour must still find the first.
    """

    deadline = deadline or time.monotonic() + 8.0
    primary_entry = CourEntry(
        anilist_id=primary_id,
        title=primary.title,
        year=primary.year,
        format=primary.format,
        episode_count=len(primary.episodes) or (primary.episode_count or 0),
        poster_url=primary.poster_url,
        banner_url=primary.banner_url,
        detail=primary,
    )

    def relation_entries(relation: str) -> list[CourEntry]:
        found: list[CourEntry] = []
        for node in (primary.relations or {}).get(relation, []):
            entry = _entry_from_relation(node)
            if entry is None or entry.anilist_id == primary_id:
                continue
            if not _is_split_cour(primary, node, entry):
                continue
            found.append(entry)
        found.sort(key=lambda item: (item.year or 9999, item.anilist_id))
        return found

    before = relation_entries("PREQUEL")
    after = relation_entries("SEQUEL")
    seen = {primary_id}
    ordered = [primary_entry]
    # Walk outwards from the primary so the nearest cours win when a franchise is
    # longer than the cap. The result is stored oldest-first below.
    for entry in reversed(before):
        if entry.anilist_id in seen or len(ordered) >= MAX_COURS:
            continue
        seen.add(entry.anilist_id)
        ordered.insert(0, entry)
    for entry in after:
        if entry.anilist_id in seen or len(ordered) >= MAX_COURS:
            continue
        seen.add(entry.anilist_id)
        ordered.append(entry)

    entries: list[CourEntry] = []
    for entry in ordered:
        if entry is primary_entry:
            entries.append(entry)
        else:
            entries.append(await _load_entry(client, cache, entry, deadline))
    return entries


def provider_season_for_anilist(session: Session, anilist_ids: list[int]) -> int:
    """The TVDB/IMDb season number that release groups use for these cours."""

    ids = [str(value) for value in anilist_ids]
    statement = (
        select(AnimeMapping.src_scope)
        .where(
            AnimeMapping.tgt_provider == "anilist",
            AnimeMapping.tgt_id.in_(ids),
            AnimeMapping.src_provider.in_(("tvdb_show", "imdb_show")),
            AnimeMapping.src_scope.like("s%"),
        )
        .order_by(AnimeMapping.src_provider, AnimeMapping.src_start)
        .limit(1)
    )
    scope = session.scalar(statement)
    if not scope:
        return 1
    try:
        return int(str(scope).lstrip("sS"))
    except ValueError:
        return 1


def build_ranges(entries: list[CourEntry], *, provider_season: int) -> list[CourRange]:
    """Turn ordered cours into contiguous library episode ranges."""

    ranges: list[CourRange] = []
    library_season = 1
    next_episode = 1
    for entry in entries:
        count = entry.episode_count
        if count <= 0:
            continue
        ranges.append(
            CourRange(
                library_season=library_season,
                provider_season=provider_season,
                start_episode=next_episode,
                end_episode=next_episode + count - 1,
                anilist_id=entry.anilist_id,
                absolute_offset=next_episode - 1,
                entry=entry,
            )
        )
        next_episode += count
    return ranges


def combined_refs(ranges: list[CourRange]) -> list[EpisodeRef]:
    """Episode rows for the whole split run, numbered continuously in the library."""

    refs: list[EpisodeRef] = []
    for item in ranges:
        detail = item.entry.detail
        provider_refs = list(detail.episodes) if detail is not None else []
        if not provider_refs:
            provider_refs = [
                EpisodeRef(season=1, episode=number)
                for number in range(1, item.entry.episode_count + 1)
            ]
        for ref in provider_refs:
            local = ref.episode - 1
            if local < 0 or local >= item.entry.episode_count:
                continue
            refs.append(
                EpisodeRef(
                    season=item.library_season,
                    episode=item.start_episode + local,
                    title=ref.title,
                    air_date=ref.air_date,
                )
            )
    return sorted(refs, key=lambda ref: (ref.season, ref.episode))


def cour_rows(ranges: list[CourRange]) -> list[dict[str, Any]]:
    return [
        {
            "library_season": item.library_season,
            "provider_season": item.provider_season,
            "start_episode": item.start_episode,
            "end_episode": item.end_episode,
            "anilist_id": item.anilist_id,
            "absolute_offset": item.absolute_offset,
        }
        for item in ranges
    ]


def season_specs(ranges: list[CourRange]) -> list[dict[str, Any]]:
    """One ``title_seasons`` row for the library season the split forms."""

    if not ranges:
        return []
    first = ranges[0]
    return [
        {
            "library_season": first.library_season,
            "provider_season": first.provider_season,
            "source": "anilist",
            "source_id": str(first.anilist_id),
            "anilist_id": first.anilist_id,
            "absolute_offset": 0,
            "episode_count": sum(item.end_episode - item.start_episode + 1 for item in ranges),
        }
    ]


async def stored_cours(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    rows: list[Any],
    deadline: float | None = None,
) -> list[CourEntry]:
    """Re-hydrate a stored split map from AniList after an identity refresh."""

    deadline = deadline or time.monotonic() + 8.0
    entries: list[CourEntry] = []
    for row in rows:
        try:
            detail, _stale = await resolve_candidate_detail(
                client,
                cache,
                source="anilist",
                source_id=str(row.anilist_id),
                kind="anime",
            )
        except Exception:
            detail = None
        if detail is not None:
            entries.append(
                CourEntry(
                    anilist_id=int(row.anilist_id),
                    title=detail.title,
                    year=detail.year,
                    format=detail.format,
                    episode_count=len(detail.episodes) or (detail.episode_count or 0),
                    poster_url=detail.poster_url,
                    banner_url=detail.banner_url,
                    detail=detail,
                )
            )
        else:
            entries.append(
                CourEntry(
                    anilist_id=int(row.anilist_id),
                    title="",
                    year=None,
                    format=None,
                    episode_count=row.end_episode - row.start_episode + 1,
                    poster_url=None,
                    banner_url=None,
                )
            )
    return entries


def ranges_from_stored(rows: list[Any], entries: list[CourEntry]) -> list[CourRange]:
    """Rebuild ranges, keeping stored boundaries when the provider is unreachable."""

    by_id = {entry.anilist_id: entry for entry in entries}
    ranges: list[CourRange] = []
    for row in rows:
        entry = by_id.get(int(row.anilist_id)) or CourEntry(
            anilist_id=int(row.anilist_id),
            title="",
            year=None,
            format=None,
            episode_count=row.end_episode - row.start_episode + 1,
            poster_url=None,
            banner_url=None,
        )
        count = entry.episode_count or (row.end_episode - row.start_episode + 1)
        ranges.append(
            CourRange(
                library_season=row.library_season,
                provider_season=row.provider_season,
                start_episode=row.start_episode,
                end_episode=row.start_episode + count - 1,
                anilist_id=entry.anilist_id,
                absolute_offset=row.absolute_offset,
                entry=entry,
            )
        )
    return ranges


__all__ = [
    "CourEntry",
    "CourRange",
    "build_ranges",
    "combined_refs",
    "cour_rows",
    "provider_season_for_anilist",
    "ranges_from_stored",
    "resolve_cours",
    "season_specs",
    "significant_tokens",
    "stored_cours",
]
