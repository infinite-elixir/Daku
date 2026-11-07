"""Torrents-CSV JSON adapter for movie and TV releases.

A DHT-wide index with a plain JSON endpoint. Daku builds the magnet locally from the
validated info hash; the payload contains no magnet and no torrent file is fetched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from daku.services.torrents.base import KeywordQuery, Release
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.magnets import build_magnet
from daku.services.torrents.matching import (
    matches_episode,
    matches_query,
    pack_covers_episode,
    pack_label,
)
from daku.services.torrents.ranking import detect_quality, extract_hints
from daku.services.torrents.throttle import throttle
from daku.services.torrents.torrentio import sanitize_filename

SEARCH_BASE = "https://torrents-csv.com/service/search"
USER_AGENT = "daku/1.0 (+self-hosted; contact in README)"
MAX_QUERY_CHARS = 120
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
SEARCH_DEADLINE_SECONDS = 8.0
MAX_ROWS = 100
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_HASH_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


class TorrentsCsvError(Exception):
    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(slots=True)
class TorrentsCsvSearch:
    releases: list[Release]
    stale: bool
    cached_at: str | None
    url: str
    queries: int = 1


def build_search_url(query: str) -> str:
    cleaned = _CONTROL_RE.sub(" ", query).strip()[:MAX_QUERY_CHARS]
    return f"{SEARCH_BASE}?size={MAX_ROWS}&q={quote_plus(cleaned)}"


def build_query(query: KeywordQuery) -> str:
    """One query per search: movie, episode or pack shape."""

    title = _CONTROL_RE.sub(" ", query.title).strip()
    if query.episode is not None and query.season is not None:
        return f"{title} S{query.season:02d}E{query.episode:02d}"
    if query.pack:
        return f"{title} Season {query.season}" if query.season is not None else title
    if query.year is not None:
        return f"{title} {query.year}"
    return title


def _hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _HASH_RE.fullmatch(candidate) else None


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return default


def _timestamp(value: object) -> str | None:
    seconds = _int(value, default=-1)
    if seconds < 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "torrents", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
    return []


def parse_results(payload: Any) -> list[Release]:
    """Map available fields to releases; malformed rows are dropped."""

    releases: list[Release] = []
    for row in _rows(payload):
        if not isinstance(row, dict):
            continue
        info_hash = _hash(row.get("infohash"))
        if info_hash is None:
            continue
        filename = sanitize_filename(row.get("name"))
        if filename is None:
            continue
        size_bytes = _int(row.get("size_bytes"), default=0)
        if size_bytes <= 0:
            continue
        metadata = {
            **extract_hints(filename),
            "leechers": _int(row.get("leechers")),
            "completed": _int(row.get("completed")),
            "published_at": _timestamp(row.get("created_unix")),
        }
        releases.append(
            Release(
                info_hash=info_hash,
                filename=filename,
                quality=detect_quality(filename),
                size_bytes=size_bytes,
                seeders=_int(row.get("seeders")),
                provider="torrents-csv",
                magnet=build_magnet(info_hash, filename),
                metadata=metadata,
            )
        )
    # Best-seeded first, and never more than the rows the request asked for. The
    # shared pipeline still dedupes, filters, sorts and slices.
    releases.sort(key=lambda release: release.seeders, reverse=True)
    return releases[:MAX_ROWS]


def _year_ok(filename: str, year: int | None) -> bool:
    if year is None:
        return True
    match = _YEAR_RE.search(filename)
    if match is None:
        return True
    return abs(int(match.group(1)) - year) <= 1


def gate_releases(releases: list[Release], query: KeywordQuery) -> list[Release]:
    """Keep only rows that answer this exact request."""

    aliases = list(query.aliases)
    primary = aliases[0] if aliases else query.title
    kept: list[Release] = []
    for release in releases:
        if not matches_query(primary, release.filename, aliases[1:]):
            continue
        if not _year_ok(release.filename, query.year):
            continue
        if query.episode is not None:
            if matches_episode(
                release.filename,
                season=query.season,
                episode=query.episode,
                pack=query.pack,
            ):
                kept.append(release)
                continue
            if not query.pack and pack_covers_episode(
                release.filename,
                season=query.season,
                episode=query.episode,
            ):
                kept.append(
                    replace(
                        release,
                        is_pack=True,
                        pack_label=pack_label(release.filename),
                    )
                )
            continue
        if query.pack:
            # A season search may surface packs; label the ones the parser saw.
            kept.append(
                replace(
                    release,
                    is_pack=True,
                    pack_label=pack_label(release.filename),
                )
            )
            continue
        kept.append(release)
    return kept


async def fetch_releases(
    client: httpx.AsyncClient,
    cache: PayloadCache,
    query: KeywordQuery,
) -> TorrentsCsvSearch:
    url = build_search_url(build_query(query))

    fresh = cache.get_fresh(url)
    if fresh is not None:
        parsed = parse_results(fresh.payload)
        return TorrentsCsvSearch(
            gate_releases(parsed, query), stale=False, cached_at=fresh.cached_at, url=url
        )

    await throttle("torrents-csv")
    try:
        response = await client.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=SEARCH_DEADLINE_SECONDS
        )
        if len(response.content) > MAX_PAYLOAD_BYTES:
            raise TorrentsCsvError(url, "response too large")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TorrentsCsvError) as exc:
        stale = cache.get_stale(url)
        if stale is not None:
            parsed = parse_results(stale.payload)
            return TorrentsCsvSearch(
                gate_releases(parsed, query), stale=True, cached_at=stale.cached_at, url=url
            )
        if isinstance(exc, TorrentsCsvError):
            raise
        raise TorrentsCsvError(url, str(exc)) from exc

    entry = cache.set(url, payload)
    parsed = parse_results(payload)
    return TorrentsCsvSearch(
        gate_releases(parsed, query), stale=False, cached_at=entry.cached_at, url=url
    )
