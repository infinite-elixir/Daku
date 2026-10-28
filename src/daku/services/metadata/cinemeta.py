"""Cinemeta catalog search and title detail adapter."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import fetch_json
from daku.services.metadata.models import EpisodeRef, TitleCandidate, TitleDetail

BASE_URL = "https://v3-cinemeta.strem.io"
IMDB_ID_RE = re.compile(r"tt\d+")
_VIDEO_ID_RE = re.compile(r"^(tt\d+):(\d+):(\d+)$")
_YEAR_RE = re.compile(r"(\d{4})")
_RUNTIME_RE = re.compile(r"(\d+)\s*(?:min|minutes?)", re.IGNORECASE)


def catalog_url(kind: str, query: str) -> str:
    return f"{BASE_URL}/catalog/{kind}/top/search={quote(query, safe='')}.json"


def meta_url(kind: str, imdb_id: str) -> str:
    return f"{BASE_URL}/meta/{kind}/{imdb_id}.json"


def _as_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value if 1800 <= value <= 2200 else None
    if isinstance(value, str):
        match = _YEAR_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_rating(value: Any) -> float | None:
    """Parse an IMDb-style 0-10 score. ``"N/A"`` and junk become ``None``."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= score <= 10.0:
        return round(score, 1)
    return None


def _as_runtime_minutes(value: Any) -> int | None:
    """Parse Cinemeta's ``"167 min"``. A bare integer is already minutes."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        match = _RUNTIME_RE.search(value)
        if match:
            minutes = int(match.group(1))
            return minutes if minutes > 0 else None
        stripped = value.strip()
        if stripped.isdigit():
            minutes = int(stripped)
            return minutes if minutes > 0 else None
    return None


def _as_names(value: Any) -> list[str]:
    """Normalise Cinemeta list fields (``["A", "B"]`` or ``"A, B"``)."""

    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        return []

    names: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            # Some payloads wrap names as {"name": "..."} objects.
            part = part.get("name")
        if not isinstance(part, str):
            continue
        cleaned = part.strip()
        if cleaned and cleaned.upper() != "N/A" and cleaned not in names:
            names.append(cleaned)
    return names


def _merge_names(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for name in group:
            if name not in merged:
                merged.append(name)
    return merged


def parse_catalog(payload: Any, kind: str) -> list[TitleCandidate]:
    if not isinstance(payload, dict):
        return []
    metas = payload.get("metas")
    if not isinstance(metas, list):
        return []

    candidates: list[TitleCandidate] = []
    for item in metas:
        if not isinstance(item, dict):
            continue
        source_id = item.get("id")
        if not isinstance(source_id, str) or not IMDB_ID_RE.fullmatch(source_id):
            continue
        title = item.get("name") or item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        poster = item.get("poster")
        candidates.append(
            TitleCandidate(
                source="cinemeta",
                source_id=source_id,
                kind=kind,
                title=title.strip(),
                year=_as_year(item.get("releaseInfo")) or _as_year(item.get("year")),
                poster_url=poster if isinstance(poster, str) and poster else None,
                raw_type=item.get("type") if isinstance(item.get("type"), str) else None,
            )
        )
    return candidates


def parse_meta(payload: Any, kind: str, imdb_id: str) -> TitleDetail | None:
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None

    title = meta.get("name") or meta.get("title") or imdb_id
    poster = meta.get("poster")
    banner = meta.get("background")
    overview = meta.get("description") or meta.get("overview")

    episodes: dict[tuple[int, int], EpisodeRef] = {}
    videos = meta.get("videos")
    if isinstance(videos, list):
        for video in videos:
            if not isinstance(video, dict):
                continue
            video_id = video.get("id")
            season = _as_int(video.get("season"))
            episode = _as_int(video.get("episode"))
            if (season is None or episode is None) and isinstance(video_id, str):
                match = _VIDEO_ID_RE.fullmatch(video_id)
                if match:
                    season = season if season is not None else int(match.group(2))
                    episode = episode if episode is not None else int(match.group(3))
            if season is None or episode is None:
                continue
            name = video.get("name") or video.get("title")
            air_date = video.get("released") or video.get("firstAired")
            episodes[(season, episode)] = EpisodeRef(
                season=season,
                episode=episode,
                title=name if isinstance(name, str) and name else None,
                imdb_id=video_id if isinstance(video_id, str) and video_id else None,
                air_date=air_date if isinstance(air_date, str) and air_date else None,
            )

    return TitleDetail(
        source="cinemeta",
        source_id=imdb_id,
        kind=kind,
        title=title if isinstance(title, str) and title else imdb_id,
        year=_as_year(meta.get("releaseInfo")) or _as_year(meta.get("year")),
        poster_url=poster if isinstance(poster, str) and poster else None,
        banner_url=banner if isinstance(banner, str) and banner else None,
        overview=overview if isinstance(overview, str) and overview else None,
        episodes=[episodes[key] for key in sorted(episodes)],
        raw_type=meta.get("type") if isinstance(meta.get("type"), str) else None,
        imdb_rating=_as_rating(meta.get("imdbRating")),
        runtime_minutes=_as_runtime_minutes(meta.get("runtime")),
        genres=_merge_names(_as_names(meta.get("genres")), _as_names(meta.get("genre"))),
        cast=_as_names(meta.get("cast"))[:10],
        directors=_as_names(meta.get("director")),
        writers=_as_names(meta.get("writer")),
    )


async def search(
    client: httpx.AsyncClient,
    query: str,
    kind: str,
    cache: TTLCache,
    *,
    deadline: float | None = None,
) -> list[TitleCandidate]:
    payload = await fetch_json(client, catalog_url(kind, query), cache, deadline=deadline)
    return parse_catalog(payload, kind)


async def fetch_detail(
    client: httpx.AsyncClient,
    kind: str,
    imdb_id: str,
    cache: TTLCache,
    *,
    deadline: float | None = None,
) -> TitleDetail | None:
    payload = await fetch_json(client, meta_url(kind, imdb_id), cache, deadline=deadline)
    return parse_meta(payload, kind, imdb_id)
