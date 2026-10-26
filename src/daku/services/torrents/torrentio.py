"""Torrentio adapter: URL building, tolerant parsing, magnet construction, cache."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from daku.services.torrents.base import Release
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.ranking import detect_quality, extract_hints, parse_size_bytes

TORRENTIO_BASE = "https://torrentio.strem.fun"
MEDIA_TYPES = ("movie", "series")

SEEDERS_RE = re.compile(r"👤[^0-9]*([0-9]+)")
SIZE_RE = re.compile(r"💾[^0-9]*([0-9][0-9.,]*\s*[KMGTP]?I?B?)", re.IGNORECASE)
PROVIDER_RE = re.compile(r"⚙\ufe0f?\s*(.+?)\s*$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
SPACE_RE = re.compile(r"\s+")
TRACKER_PREFIX = "tracker:"


class TorrentioError(Exception):
    """Torrentio could not be reached and no stale payload exists."""

    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(slots=True)
class ReleaseSearch:
    releases: list[Release]
    stale: bool
    cached_at: str | None
    url: str


def build_prefix(
    *,
    providers: str | None = None,
    qualityfilter: str | None = None,
    sort: str | None = None,
) -> str:
    """Build a Torrentio config prefix exactly like tsearch.

    Empty parts are omitted; parts are joined with a single ``|``.
    """

    parts: list[str] = []
    if providers is not None and providers.strip():
        parts.append(f"providers={providers.strip()}")
    if qualityfilter is not None and qualityfilter.strip():
        parts.append(f"qualityfilter={qualityfilter.strip()}")
    if sort is not None and sort.strip():
        parts.append(f"sort={sort.strip()}")
    return "|".join(parts)


def normalise_prefix(config: str | None) -> str:
    """Trim a stored config prefix without reordering its parts."""

    if not config:
        return ""
    parts = [part.strip() for part in config.split("|")]
    return "|".join(part for part in parts if part)


def with_qualityfilter(config: str | None, labels: list[str]) -> str:
    """Align a configured ``qualityfilter=`` part with the effective filter.

    The same list drives both the local filter and the upstream prefix, so
    ``quality_filter=[]`` really re-enables cam/scr instead of asking Torrentio to
    drop them again. A config without a ``qualityfilter`` part is left untouched;
    the local filter still applies.
    """

    value = ",".join(label for label in labels if label)
    parts: list[str] = []
    for part in normalise_prefix(config).split("|"):
        if not part:
            continue
        if part.lower().startswith("qualityfilter="):
            existing = part.split("=", 1)[1]
            existing_labels = [
                label.strip().lower() for label in existing.split(",") if label.strip()
            ]
            if value and sorted(existing_labels) != sorted(label.lower() for label in labels):
                parts.append(f"qualityfilter={value}")
            elif value:
                # Same set in a different order: keep the configured spelling/order
                # so the cache key does not churn.
                parts.append(part)
            continue
        parts.append(part)
    return "|".join(parts)


def with_providers(config: str | None, providers: list[str]) -> str:
    """Replace (or add) the ``providers=`` part of a Torrentio config prefix.

    An empty list means "keep the configured default"; whitespace and duplicate
    entries are dropped. Preserving the part's position keeps the resulting cache
    key stable when a user toggles back to a previous selection.
    """

    cleaned = [provider.strip().lower() for provider in providers if provider.strip()]
    parts = [part for part in normalise_prefix(config).split("|") if part]

    if not cleaned:
        return "|".join(parts)

    value = ",".join(dict.fromkeys(cleaned))
    for index, part in enumerate(parts):
        if part.lower().startswith("providers="):
            parts[index] = f"providers={value}"
            return "|".join(parts)
    return "|".join([f"providers={value}", *parts])


def build_stream_url(config: str | None, media_type: str, media_id: str) -> str:
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"unsupported Torrentio media type: {media_type}")
    if not media_id:
        raise ValueError("Torrentio media id must not be empty")
    prefix = normalise_prefix(config)
    path = f"/stream/{media_type}/{media_id}.json"
    if prefix:
        return f"{TORRENTIO_BASE}/{prefix}{path}"
    return f"{TORRENTIO_BASE}{path}"


def build_magnet(
    info_hash: str | None,
    filename: str | None,
    sources: Any = None,
) -> str | None:
    """Build ``magnet:?xt=urn:btih:<hash>&dn=<name>&tr=<tracker>...``.

    Only ``tracker:`` sources are included, in input order, deduplicated. ``dht:``
    sources are intentionally skipped.
    """

    if not isinstance(info_hash, str) or not info_hash.strip():
        return None
    if not isinstance(filename, str) or not filename.strip():
        return None

    trackers: list[str] = []
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, str) or not source.startswith(TRACKER_PREFIX):
                continue
            tracker = source[len(TRACKER_PREFIX) :].strip()
            if tracker and tracker not in trackers:
                trackers.append(tracker)

    magnet = f"magnet:?xt=urn:btih:{info_hash.strip().lower()}&dn={quote(filename, safe='')}"
    for tracker in trackers:
        magnet += f"&tr={quote(tracker, safe='')}"
    return magnet


def sanitize_filename(value: str | None) -> str | None:
    """Make a Torrentio filename safe to display and store."""

    if not isinstance(value, str):
        return None
    cleaned = value.replace("/", "_").replace("\\", "_")
    cleaned = CONTROL_RE.sub(" ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip()
    return cleaned or None


def _info_line(title: str) -> str:
    for line in title.replace("\r", "").split("\n"):
        if "👤" in line or "💾" in line or "⚙" in line:
            return line
    return ""


def _parse_seeders(info: str) -> int:
    match = SEEDERS_RE.search(info)
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _parse_size(info: str) -> int | None:
    match = SIZE_RE.search(info)
    if match is None:
        return None
    return parse_size_bytes(match.group(1))


def _parse_provider(info: str) -> str:
    match = PROVIDER_RE.search(info)
    if match is None:
        return "unknown"
    provider = match.group(1).strip()
    return provider or "unknown"


def parse_stream(stream: Any) -> Release | None:
    """Parse one Torrentio stream. Returns ``None`` when a required field is missing."""

    if not isinstance(stream, dict):
        return None

    info_hash = stream.get("infoHash")
    if not isinstance(info_hash, str) or not info_hash.strip():
        return None

    title = stream.get("title")
    if not isinstance(title, str):
        title = ""

    hints = stream.get("behaviorHints")
    raw_filename = hints.get("filename") if isinstance(hints, dict) else None
    filename = sanitize_filename(raw_filename)
    if filename is None:
        # Torrentio always sends behaviorHints.filename; without it the row cannot be
        # tied to a file, so drop it instead of inventing a name.
        return None

    info = _info_line(title)
    seeders = _parse_seeders(info)
    size_bytes = _parse_size(info)
    provider = _parse_provider(info)
    quality = detect_quality(f"{filename}\n{title}")

    sources = stream.get("sources")
    magnet = build_magnet(info_hash, filename, sources)
    if magnet is None:
        return None

    return Release(
        info_hash=info_hash.strip().lower(),
        filename=filename,
        quality=quality,
        size_bytes=size_bytes,
        seeders=seeders,
        provider=provider,
        magnet=magnet,
        metadata=extract_hints(filename, title),
    )


def parse_streams(payload: Any) -> list[Release]:
    """Parse a raw Torrentio payload, skipping malformed entries."""

    if not isinstance(payload, dict):
        return []
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return []

    releases: list[Release] = []
    for stream in streams:
        try:
            release = parse_stream(stream)
        except Exception:
            # A single malformed row must never break the whole listing.
            continue
        if release is not None:
            releases.append(release)
    return releases


async def fetch_releases(
    client: httpx.AsyncClient,
    cache: PayloadCache,
    config: str | None,
    media_type: str,
    media_id: str,
) -> ReleaseSearch:
    """Fetch (or reuse) a raw payload, then parse it on every call."""

    url = build_stream_url(config, media_type, media_id)

    fresh = cache.get_fresh(url)
    if fresh is not None:
        return ReleaseSearch(
            releases=parse_streams(fresh.payload),
            stale=False,
            cached_at=fresh.cached_at,
            url=url,
        )

    failure: str | None = None
    payload: Any = None
    try:
        response = await client.get(url)
        if 200 <= response.status_code < 300:
            payload = response.json()
        else:
            failure = f"HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        failure = str(exc)

    if failure is not None:
        stale = cache.get_stale(url)
        if stale is not None:
            return ReleaseSearch(
                releases=parse_streams(stale.payload),
                stale=True,
                cached_at=stale.cached_at,
                url=url,
            )
        raise TorrentioError(url, failure)

    entry = cache.set(url, payload)
    return ReleaseSearch(
        releases=parse_streams(payload),
        stale=False,
        cached_at=entry.cached_at,
        url=url,
    )


class TorrentioProvider:
    """``ReleaseProvider`` implementation backed by :func:`fetch_releases`."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: PayloadCache,
        config: str | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._config = config

    async def search(self, media_type: str, media_id: str) -> list[Release]:
        result = await fetch_releases(self._client, self._cache, self._config, media_type, media_id)
        return result.releases
