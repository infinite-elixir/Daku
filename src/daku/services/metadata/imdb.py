"""IMDb suggestion adapter.

Endpoint: ``GET https://v2.sg.media-imdb.com/suggestion/{first}/{normalised}.json``
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import fetch_json
from daku.services.metadata.models import TitleCandidate

BASE_URL = "https://v2.sg.media-imdb.com/suggestion"
IMDB_ID_RE = re.compile(r"tt\d+")
_YEAR_RE = re.compile(r"(\d{4})")

_MOVIE_TYPES = frozenset({"feature", "short", "video", "movie", "featurefilm"})
_SERIES_TYPES = frozenset({"tvseries", "tvminiseries", "tvmovie", "tvspecial", "series", "tv"})


def normalise_query(query: str) -> str:
    """Lowercase, spaces to underscores, and collapse unsupported characters."""

    lowered = query.strip().lower()
    replaced = re.sub(r"[^a-z0-9_.-]", "_", lowered)
    return re.sub(r"_+", "_", replaced)


def build_suggestion_url(query: str) -> str:
    normalised = normalise_query(query)
    first = normalised[0] if normalised else "_"
    return f"{BASE_URL}/{first}/{normalised}.json"


def _clean_type(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _map_kind(raw_type: str | None) -> str:
    if not raw_type:
        return "movie"
    token = re.sub(r"[\s_-]+", "", raw_type.lower())
    if token in _SERIES_TYPES:
        return "series"
    if token in _MOVIE_TYPES:
        return "movie"
    return "movie"


def _as_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value if 1800 <= value <= 2200 else None
    if isinstance(value, str):
        match = _YEAR_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def _poster(item: dict[str, Any]) -> str | None:
    image = item.get("i")
    if isinstance(image, dict):
        url = image.get("imageUrl") or image.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def parse_suggestions(payload: Any) -> list[TitleCandidate]:
    """Turn an IMDb suggestion payload into candidates, dropping unusable rows."""

    if not isinstance(payload, dict):
        return []
    rows = payload.get("d")
    if not isinstance(rows, list):
        return []

    candidates: list[TitleCandidate] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        source_id = item.get("id")
        if not isinstance(source_id, str) or not IMDB_ID_RE.fullmatch(source_id):
            continue
        title = item.get("l") or item.get("name") or item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue

        raw_type = (
            _clean_type(item.get("qid"))
            or _clean_type(item.get("q"))
            or _clean_type(item.get("type"))
        )
        year = _as_year(item.get("y")) or _as_year(item.get("yr"))

        candidates.append(
            TitleCandidate(
                source="imdb",
                source_id=source_id,
                kind=_map_kind(raw_type),
                title=title.strip(),
                year=year,
                poster_url=_poster(item),
                raw_type=raw_type,
            )
        )
    return candidates


async def search(
    client: httpx.AsyncClient,
    query: str,
    cache: TTLCache,
    *,
    deadline: float | None = None,
) -> list[TitleCandidate]:
    url = build_suggestion_url(query)
    payload = await fetch_json(client, url, cache, deadline=deadline)
    return parse_suggestions(payload)
