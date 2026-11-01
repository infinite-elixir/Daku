"""Artwork URL checks (T-052).

Poster/banner URLs decay: Metahub returns ``404`` for some IMDb ids and a refresh that
fails kept pointing at them forever. This module answers one question with a small
cache: is this URL still serving an image?
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

CHECK_TTL_SECONDS = 24 * 60 * 60
CHECK_TIMEOUT_SECONDS = 4.0
# Statuses that mean "this URL is usable artwork". Redirects are followed first, so
# a 3xx only survives here when ``follow_redirects`` could not resolve it.
USABLE_STATUSES = frozenset({200, 206, 304})
DEAD_STATUSES = frozenset({404, 410})
_VOLATILE_HOSTS = ("images.metahub.space", "live.metahub.space")
_cache: dict[str, tuple[float, bool]] = {}


def is_volatile(url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url).hostname or ""
    return any(host == value or host.endswith(f".{value}") for value in _VOLATILE_HOSTS)


def cached(url: str) -> bool | None:
    entry = _cache.get(url)
    if entry is None:
        return None
    checked_at, alive = entry
    if time.monotonic() - checked_at > CHECK_TTL_SECONDS:
        _cache.pop(url, None)
        return None
    return alive


def remember(url: str, alive: bool) -> None:
    _cache[url] = (time.monotonic(), alive)


async def artwork_ok(client: httpx.AsyncClient, url: str | None) -> bool:
    """Return whether ``url`` still serves usable artwork.

    ``200``/``206``/``304`` are usable and ``404``/``410`` are dead. Other failures
    are treated conservatively for Metahub (the provider known to rot) but do not
    discard an otherwise healthy AniList/TMDB CDN URL when the network is down.
    """

    if not url:
        return False
    known = cached(url)
    if known is not None:
        return known
    try:
        response = await client.head(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(CHECK_TIMEOUT_SECONDS),
        )
        if response.status_code in (405, 501):
            response = await client.get(
                url,
                follow_redirects=True,
                headers={"Range": "bytes=0-0"},
                timeout=httpx.Timeout(CHECK_TIMEOUT_SECONDS),
            )
        if response.status_code in USABLE_STATUSES:
            alive = True
        elif response.status_code in DEAD_STATUSES:
            alive = False
        else:
            alive = not is_volatile(url)
    except Exception:
        # An artwork probe must never break a metadata refresh. Unknown status on a
        # rotting provider host is treated as dead; elsewhere the URL is kept.
        alive = not is_volatile(url)
    remember(url, alive)
    return alive


async def url_is_live(client: httpx.AsyncClient, url: str | None) -> bool:
    """Backwards-compatible alias for :func:`artwork_ok`."""

    return await artwork_ok(client, url)


def clear_cache() -> None:
    """Test hook. Production only relies on the TTL."""

    _cache.clear()


__all__ = [
    "artwork_ok",
    "cached",
    "clear_cache",
    "is_volatile",
    "remember",
    "url_is_live",
]
