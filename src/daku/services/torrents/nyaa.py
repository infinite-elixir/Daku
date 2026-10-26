"""Nyaa.si RSS adapter for anime releases."""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from daku.services.torrents.base import Release
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.ranking import detect_quality, extract_hints, parse_size_bytes
from daku.services.torrents.torrentio import sanitize_filename

NYAA_BASE = "https://nyaa.si/"
NYAA_CATEGORY = "1_2"
NYAA_CATEGORIES = frozenset({"1_1", "1_2", "1_3"})
NYAA_TRACKERS = (
    "udp://nyaa.tracker.wf:7777/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.tamersunion.org:443/announce",
)
NYAA_USER_AGENT = "daku/1.0 (+self-hosted; contact in README)"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_HASH_RE = re.compile(r"^(?:[0-9a-f]{40}|[a-z2-7]{32})$", re.IGNORECASE)
_NS = {"nyaa": "https://nyaa.si/xmlns/nyaa"}


class NyaaError(Exception):
    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(slots=True)
class NyaaSearch:
    releases: list[Release]
    stale: bool
    cached_at: str | None
    url: str


def build_search_url(query: str, category: str = NYAA_CATEGORY) -> str:
    categories = ",".join(
        item for item in (part.strip() for part in category.split(",")) if item in NYAA_CATEGORIES
    )
    if not categories:
        categories = NYAA_CATEGORY
    cleaned = _CONTROL_RE.sub(" ", query).strip()[:120]
    return f"{NYAA_BASE}?page=rss&q={quote(cleaned, safe='')}&c={quote(categories, safe=',')}&f=0&s=seeders&o=desc"


def build_magnet(info_hash: str, title: str) -> str:
    encoded_title = quote(title, safe="")
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={encoded_title}" + "".join(
        f"&tr={quote(tracker, safe='')}" for tracker in NYAA_TRACKERS
    )


def _text(item: ET.Element, name: str) -> str | None:
    node = item.find(name)
    if node is None:
        node = item.find(f"nyaa:{name}", _NS)
    if node is None or node.text is None:
        return None
    value = html.unescape(node.text).strip()
    return value or None


def _hash(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower()
    if _HASH_RE.fullmatch(candidate):
        if len(candidate) == 32:
            try:
                return base64.b32decode(candidate.upper()).hex()
            except (binascii.Error, ValueError):
                return None
        return candidate
    return None


def parse_rss(payload: str | bytes) -> list[Release]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    releases: list[Release] = []
    for item in root.findall(".//item"):
        title = _text(item, "title")
        info_hash = _hash(_text(item, "infoHash"))
        if not title or not info_hash:
            continue
        filename = sanitize_filename(title)
        if filename is None:
            continue
        size = parse_size_bytes(_text(item, "size"))
        seeders = (
            int(_text(item, "seeders") or 0) if (_text(item, "seeders") or "0").isdigit() else 0
        )
        metadata = {
            **extract_hints(filename),
            "source": "nyaa",
            "category": _text(item, "categoryId"),
        }
        for key in ("trusted", "remake"):
            value = _text(item, key)
            if value is not None:
                metadata[key] = value.lower() in {"1", "true", "yes"}
        releases.append(
            Release(
                info_hash=info_hash,
                filename=filename,
                quality=detect_quality(filename),
                size_bytes=size,
                seeders=seeders,
                provider="nyaa",
                magnet=build_magnet(info_hash, title),
                metadata=metadata,
            )
        )
    return releases


_throttle_lock = asyncio.Lock()
_last_request = 0.0


async def fetch_releases(
    client: httpx.AsyncClient,
    cache: PayloadCache,
    query: str,
    *,
    category: str = NYAA_CATEGORY,
) -> NyaaSearch:
    global _last_request
    url = build_search_url(query, category)
    fresh = cache.get_fresh(url)
    if fresh is not None:
        return NyaaSearch(parse_rss(fresh.payload), False, fresh.cached_at, url)
    async with _throttle_lock:
        wait = 1.0 - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        try:
            response = await client.get(url, headers={"User-Agent": NYAA_USER_AGENT})
            if response.status_code in (429, 503):
                raise NyaaError(url, f"HTTP {response.status_code}")
            response.raise_for_status()
            entry = cache.set(url, response.text)
            return NyaaSearch(parse_rss(response.text), False, entry.cached_at, url)
        except (httpx.HTTPError, NyaaError) as exc:
            stale = cache.get_stale(url)
            if stale is not None:
                return NyaaSearch(parse_rss(stale.payload), True, stale.cached_at, url)
            if isinstance(exc, NyaaError):
                raise
            raise NyaaError(url, str(exc)) from exc


def matches_query(title: str, filename: str, synonyms: list[str] | None = None) -> bool:
    def tokens(value: str) -> set[str]:
        return {part for part in re.findall(r"[a-z0-9]+", value.casefold()) if len(part) > 1}

    names = [title, *(synonyms or [])]
    haystack = tokens(filename)
    return any(tokens(name) & haystack for name in names if name.strip())


def matches_episode(
    filename: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    absolute: int | None = None,
    pack: bool = False,
) -> bool:
    """Match common scene episode notation without attaching batches to one episode."""

    if episode is None or pack:
        return True
    text = filename.casefold()
    number = str(episode)
    season_number = str(season or 0)
    patterns = (
        rf"s0?{re.escape(season_number)}e0?{re.escape(number)}",
        rf"0?{re.escape(season_number)}x0?{re.escape(number)}",
        rf"\be0?{re.escape(number)}\b",
        rf"\bepisode[ ._-]*0?{re.escape(number)}\b",
        rf"\[\s*0?{re.escape(number)}\s*\]",
        rf"(?<!\d)-\s*0?{re.escape(number)}(?:\D|$)",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        return True
    return absolute is not None and bool(re.search(rf"\b0?{re.escape(str(absolute))}\b", text))
