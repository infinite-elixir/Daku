"""Jikan v4 episode titles and air dates (T-036).

AniList only exposes an episode count, so episode names come from Jikan's MAL
episode list. Jikan has no ``episode`` field: the number is the position in the
full ordered list, which is why pagination is flattened before it is matched.

Nothing in this module raises into Add or refresh: a failed request returns an
empty list (or a stale cached list) and is logged once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import UpstreamError, fetch_json

BASE_URL = "https://api.jikan.moe/v4"
MAX_PAGES = 20
MAX_EPISODES = 2000
MIN_INTERVAL = 1 / 3
CACHE_TTL = 6 * 60 * 60

logger = logging.getLogger("daku.metadata.jikan")

# Parsed lists live longer than the shared payload cache (6 h, not 1 h).
_episode_cache = TTLCache(CACHE_TTL)
_throttle_lock = asyncio.Lock()
_last_request_at = 0.0
_logged_failures: set[int] = set()


@dataclass(slots=True)
class JikanEpisode:
    number: int
    title: str | None
    air_date: str | None
    filler: bool = False
    recap: bool = False


def clear_cache() -> None:
    _episode_cache.clear()
    _logged_failures.clear()


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _flag(value: Any) -> bool:
    return value is True


def parse_episodes(payload: Any, *, offset: int = 0) -> tuple[list[JikanEpisode], int | None]:
    """Parse one page. Returns the rows and ``last_visible_page`` when present."""

    if not isinstance(payload, dict):
        return [], None
    data = payload.get("data")
    if not isinstance(data, list):
        return [], None

    episodes: list[JikanEpisode] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        # Number by parsed position, not raw index: page offsets are the parsed
        # count, so a malformed row must not consume a number or pages overlap.
        number = offset + len(episodes) + 1
        title = _text(item.get("title"))
        air_date = _text(item.get("aired"))
        episodes.append(
            JikanEpisode(
                number=number,
                title=title,
                air_date=air_date,
                filler=_flag(item.get("filler")),
                recap=_flag(item.get("recap")),
            )
        )

    last_page: int | None = None
    pagination = payload.get("pagination")
    if isinstance(pagination, dict):
        raw = pagination.get("last_visible_page")
        if isinstance(raw, int) and raw > 0:
            last_page = raw
    return episodes, last_page


async def _throttle() -> None:
    global _last_request_at
    async with _throttle_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _cache_key(mal_id: int) -> str:
    return f"jikan:episodes:{mal_id}"


def cached_episodes(mal_id: int) -> list[JikanEpisode] | None:
    entry = _episode_cache.get_stale(_cache_key(mal_id))
    return entry.value if entry is not None else None


async def fetch_episode_titles(
    client: httpx.AsyncClient,
    cache: TTLCache,
    mal_id: int,
    *,
    episode_count: int | None = None,
    deadline: float | None = None,
) -> list[JikanEpisode]:
    """Return the ordered MAL episode list. Failures are swallowed."""

    if mal_id <= 0:
        return []
    key = _cache_key(mal_id)
    fresh = _episode_cache.get_fresh(key)
    if fresh is not None:
        return fresh.value

    limit = MAX_EPISODES
    if episode_count is not None and episode_count > 0:
        limit = min(limit, episode_count)

    episodes: list[JikanEpisode] = []
    page = 1
    last_page = 1
    try:
        while page <= min(last_page, MAX_PAGES) and len(episodes) < limit:
            if deadline is not None and deadline - time.monotonic() <= 0.05:
                break
            await _throttle()
            url = f"{BASE_URL}/anime/{mal_id}/episodes?page={page}"
            payload = await fetch_json(client, url, cache, deadline=deadline)
            parsed, reported_last = parse_episodes(payload, offset=len(episodes))
            if reported_last is not None:
                last_page = reported_last
            if not parsed:
                break
            episodes.extend(parsed)
            page += 1
    except UpstreamError as exc:
        if mal_id not in _logged_failures:
            _logged_failures.add(mal_id)
            logger.warning(
                "jikan episode titles unavailable",
                extra={"mal_id": mal_id, "status": exc.status_code},
            )
        if not episodes:
            stale = cached_episodes(mal_id)
            if stale is not None:
                return stale
            return []
    except Exception:
        if mal_id not in _logged_failures:
            _logged_failures.add(mal_id)
            logger.warning("jikan episode titles unavailable", extra={"mal_id": mal_id})
        if not episodes:
            return cached_episodes(mal_id) or []
        return episodes

    if episodes:
        _episode_cache.set(key, episodes[:limit])
    return episodes[:limit]


def names_by_number(episodes: list[JikanEpisode]) -> dict[int, JikanEpisode]:
    return {episode.number: episode for episode in episodes}


__all__ = [
    "JikanEpisode",
    "cached_episodes",
    "clear_cache",
    "fetch_episode_titles",
    "names_by_number",
    "parse_episodes",
]
