"""AnimeTosho JSON feed adapter for anime releases.

The feed is a public, read-only index of anime torrents. Daku reads metadata only and
builds its own magnet from the validated info hash; it never downloads a ``.torrent``.
"""

from __future__ import annotations

import base64
import binascii
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx

from daku.services.metadata.aliases import derive_styles, normalise_query
from daku.services.torrents.base import Release
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.magnets import PUBLIC_TRACKERS, build_magnet
from daku.services.torrents.matching import (
    MATCH_STOPWORDS,
    ReleaseEpisode,
    matches_episode,
    matches_query,
    pack_covers_episode,
    pack_label,
    parse_release_episode,
)
from daku.services.torrents.ranking import detect_quality, extract_hints, parse_size_bytes
from daku.services.torrents.throttle import throttle
from daku.services.torrents.torrentio import sanitize_filename

__all__ = [
    "MATCH_STOPWORDS",
    "PUBLIC_TRACKERS",
    "AnimeToshoError",
    "AnimeToshoSearch",
    "ReleaseEpisode",
    "build_bare_queries",
    "build_episode_queries",
    "build_magnet",
    "build_query_plan",
    "build_search_url",
    "fetch_releases",
    "matches_episode",
    "matches_query",
    "pack_covers_episode",
    "pack_label",
    "parse_feed",
    "parse_release_episode",
]

FEED_BASE = "https://feed.animetosho.net/"
LEGACY_FEED_BASE = "https://feed.animetosho.xyz/"
FEED_USER_AGENT = "daku/1.0 (+self-hosted; contact in README)"
MAX_QUERY_CHARS = 120
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_QUERIES = 8
MAX_CANDIDATES = 40
SEARCH_DEADLINE_SECONDS = 8.0
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_HASH_RE = re.compile(r"^(?:[0-9a-f]{40}|[a-z2-7]{32})$", re.IGNORECASE)


class AnimeToshoError(Exception):
    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(slots=True)
class AnimeToshoSearch:
    releases: list[Release]
    stale: bool
    cached_at: str | None
    url: str


def build_search_url(query: str) -> str:
    cleaned = _CONTROL_RE.sub(" ", query).strip()[:MAX_QUERY_CHARS]
    return f"{FEED_BASE}json?q={quote_plus(cleaned)}"


def _hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not _HASH_RE.fullmatch(candidate):
        return None
    if len(candidate) == 32:
        try:
            return base64.b32decode(candidate.upper()).hex()
        except (binascii.Error, ValueError):
            return None
    return candidate


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return 0


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _timestamp(value: object) -> str | None:
    seconds = _optional_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = html.unescape(value).strip()
    return cleaned or None


def parse_feed(payload: str | bytes) -> list[Release]:
    try:
        results = json.loads(payload)
    except (TypeError, ValueError):
        return []
    if not isinstance(results, list):
        return []

    releases: list[Release] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        info_hash = _hash(result.get("info_hash"))
        if info_hash is None:
            continue
        title = _text(result.get("title")) or _text(result.get("torrent_name"))
        if title is None:
            continue
        filename = sanitize_filename(title)
        if filename is None:
            continue

        metadata = {
            **extract_hints(filename),
            "category": _text(result.get("article_title")),
            "status": _text(result.get("status")),
            "num_files": _optional_int(result.get("num_files")),
            "published_at": _timestamp(result.get("timestamp")),
        }
        releases.append(
            Release(
                info_hash=info_hash,
                filename=filename,
                quality=detect_quality(filename),
                size_bytes=parse_size_bytes(result.get("total_size")),
                seeders=_int(result.get("seeders")),
                provider="animetosho",
                magnet=build_magnet(info_hash, title),
                metadata=metadata,
            )
        )
    return releases


def _alias_styles(aliases: list[str]) -> list[list[str]]:
    return [derive_styles(alias) for alias in aliases if alias.strip()]


def _dedupe_queries(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _CONTROL_RE.sub(" ", value).strip()
        key = normalise_query(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def build_bare_queries(aliases: list[str]) -> list[str]:
    """Every alias once before spending the rest of the budget on extra styles."""

    styles = _alias_styles(aliases)
    ordered = [group[0] for group in styles if group]
    for group in styles:
        ordered.extend(group[1:])
    return _dedupe_queries(ordered)[:MAX_QUERIES]


def _season_numbers(season: int | None, provider_seasons: list[int] | None) -> list[int]:
    values = list(dict.fromkeys(value for value in (provider_seasons or []) if value is not None))
    if not values and season is not None:
        values = [season]
    return values


def build_episode_queries(
    aliases: list[str],
    *,
    season: int | None = None,
    episode: int | None = None,
    provider_seasons: list[int] | None = None,
) -> list[str]:
    """Suffix spellings, provider-season ``SxxEyy`` first, then episode-only forms."""

    if episode is None:
        return []
    styles = _alias_styles(aliases)
    ordered: list[str] = []
    seasons = _season_numbers(season, provider_seasons)
    for group in styles:
        primary = group[0] if group else None
        if primary is None:
            continue
        for provider_season in seasons:
            ordered.append(f"{primary} S{provider_season:02d}E{episode:02d}")
    for group in styles:
        primary = group[0] if group else None
        if primary is None:
            continue
        ordered.append(f"{primary} E{episode:02d}")
        ordered.append(f"{primary} Episode {episode}")
        ordered.append(f"{primary} {episode:02d}")
    for group in styles:
        for extra in group[1:]:
            ordered.append(f"{extra} {episode:02d}")
    return _dedupe_queries(ordered)


def build_query_plan(
    aliases: list[str],
    *,
    season: int | None = None,
    episode: int | None = None,
    provider_seasons: list[int] | None = None,
) -> list[str]:
    """The bounded plan: bare names first, then explicit episode spellings."""

    plan = build_bare_queries(aliases) + build_episode_queries(
        aliases,
        season=season,
        episode=episode,
        provider_seasons=provider_seasons,
    )
    return _dedupe_queries(plan)[:MAX_QUERIES]


async def fetch_releases(
    client: httpx.AsyncClient,
    cache: PayloadCache,
    query: str,
) -> AnimeToshoSearch:
    url = build_search_url(query)
    # Entries written before the feed moved to ``.net`` are still valid.
    legacy_url = url.replace(FEED_BASE, LEGACY_FEED_BASE, 1)
    fresh = cache.get_fresh(url) or cache.get_fresh(legacy_url)
    if fresh is not None:
        return AnimeToshoSearch(parse_feed(fresh.payload), False, fresh.cached_at, url)
    await throttle("animetosho")
    try:
        response = await client.get(url, headers={"User-Agent": FEED_USER_AGENT})
        if response.status_code in (429, 503):
            raise AnimeToshoError(url, f"HTTP {response.status_code}")
        if len(response.content) > MAX_PAYLOAD_BYTES:
            raise AnimeToshoError(url, "response too large")
        response.raise_for_status()
        entry = cache.set(url, response.text)
        return AnimeToshoSearch(parse_feed(response.text), False, entry.cached_at, url)
    except (httpx.HTTPError, AnimeToshoError) as exc:
        stale = cache.get_stale(url) or cache.get_stale(legacy_url)
        if stale is not None:
            return AnimeToshoSearch(parse_feed(stale.payload), True, stale.cached_at, url)
        if isinstance(exc, AnimeToshoError):
            raise
        raise AnimeToshoError(url, str(exc)) from exc
