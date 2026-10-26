"""Optional OMDb enrichment for Rotten Tomatoes and Metacritic scores.

OMDb is a *secondary* source: Cinemeta's ``imdbRating`` always wins, and an OMDb
disagreement is never rendered as a second IMDb number. Without an API key this module
returns empty ratings and makes no network request.

The API key is passed as a query parameter but is never part of a cache key, an error
message or a log line. A ``401``/``Invalid API key`` response disables enrichment for
the life of the process so a bad key costs one request, not one per title.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import REQUEST_BUDGET, UpstreamError, fetch_json
from daku.services.metadata.models import TitleDetail

OMDB_URL = "https://www.omdbapi.com/"
SUCCESS_TTL_SECONDS = 24 * 60 * 60
FAILURE_TTL_SECONDS = 5 * 60
IMDB_ID_RE = re.compile(r"^tt\d+$")

STATUS_CONFIGURED = "configured"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_INVALID_KEY = "invalid-key"

_INVALID_KEY_HINTS = ("invalid api key", "invalid api", "no api key")


@dataclass(slots=True)
class Ratings:
    imdb_rating: float | None = None
    rt_rating: int | None = None
    metacritic_rating: int | None = None

    def any(self) -> bool:
        return (
            self.imdb_rating is not None
            or self.rt_rating is not None
            or self.metacritic_rating is not None
        )


def _as_imdb_rating(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= score <= 10.0:
        return round(score, 1)
    return None


def _as_percentage(value: Any) -> int | None:
    """Parse ``"92%"`` / ``"89/100"`` / ``89`` into 0-100, or ``None``."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 100 else None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    elif "/" in text:
        text = text.split("/", 1)[0].strip()
    try:
        score = float(text)
    except ValueError:
        return None
    if 0.0 <= score <= 100.0:
        return round(score)
    return None


def parse_ratings(payload: Any) -> Ratings:
    """Extract ratings from an OMDb response. Never raises on malformed values."""

    if not isinstance(payload, dict):
        return Ratings()
    if str(payload.get("Response", "")).lower() == "false":
        return Ratings()

    ratings = Ratings(imdb_rating=_as_imdb_rating(payload.get("imdbRating")))
    entries = payload.get("Ratings")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source = entry.get("Source")
            if not isinstance(source, str):
                continue
            lowered = source.strip().lower()
            if lowered == "rotten tomatoes":
                ratings.rt_rating = _as_percentage(entry.get("Value"))
            elif lowered == "metacritic":
                ratings.metacritic_rating = _as_percentage(entry.get("Value"))
    return ratings


def _is_invalid_key(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("Response", "")).lower() != "false":
        return False
    error = payload.get("Error")
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return any(hint in lowered for hint in _INVALID_KEY_HINTS)


def ratings_payload(
    ratings: Ratings, *, stale: bool = False, sources: list[str] | None = None
) -> dict[str, Any]:
    """Response shape shared by the preview and title-metadata endpoints."""

    return {
        "imdb_rating": ratings.imdb_rating,
        "rt_rating": ratings.rt_rating,
        "metacritic_rating": ratings.metacritic_rating,
        "stale": stale,
        "sources": list(sources or []),
    }


def display_payload(
    detail: TitleDetail,
    ratings: Ratings,
    *,
    stale: bool = False,
) -> dict[str, Any]:
    """Merge a :class:`TitleDetail` with optional OMDb ratings for the display API.

    Cinemeta's IMDb rating wins whenever both sources have one; OMDb only fills a gap.
    Nothing here is written to the database.
    """

    sources = [detail.source]
    if ratings.any():
        sources.append("omdb")

    return {
        "imdb_rating": detail.imdb_rating
        if detail.imdb_rating is not None
        else ratings.imdb_rating,
        "rt_rating": ratings.rt_rating,
        "metacritic_rating": ratings.metacritic_rating,
        "runtime_minutes": detail.runtime_minutes,
        "genres": list(detail.genres),
        "cast": list(detail.cast[:10]),
        "directors": list(detail.directors),
        "writers": list(detail.writers),
        "studios": list(detail.studios),
        "stale": stale,
        "sources": sources,
    }


class OmdbEnricher:
    """Process-local OMDb cache and disable switch."""

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._success = TTLCache(ttl_seconds=SUCCESS_TTL_SECONDS, clock=clock)
        self._failure = TTLCache(ttl_seconds=FAILURE_TTL_SECONDS, clock=clock)
        self.disabled = False

    def status(self, api_key: str) -> str:
        if not api_key:
            return STATUS_UNCONFIGURED
        if self.disabled:
            return STATUS_INVALID_KEY
        return STATUS_CONFIGURED

    async def ratings(
        self,
        client: httpx.AsyncClient,
        imdb_id: str | None,
        api_key: str | None,
    ) -> Ratings:
        if not api_key or self.disabled:
            return Ratings()
        if not imdb_id or not IMDB_ID_RE.fullmatch(imdb_id):
            return Ratings()

        key = f"omdb:{imdb_id}"
        if self._failure.get_fresh(key) is not None:
            return Ratings()

        try:
            payload = await fetch_json(
                client,
                OMDB_URL,
                self._success,
                deadline=time.monotonic() + REQUEST_BUDGET,
                cache_key=key,
                params={"apikey": api_key, "i": imdb_id, "plot": "short"},
            )
        except UpstreamError as exc:
            if exc.status_code == 401:
                self.disabled = True
                self._success.delete(key)
                return Ratings()
            self._remember_failure(key)
            return Ratings()

        if _is_invalid_key(payload):
            self.disabled = True
            self._success.delete(key)
            return Ratings()

        ratings = parse_ratings(payload)
        if not ratings.any():
            # OMDb answers "not found" with HTTP 200; do not keep that for 24 h.
            self._success.delete(key)
            self._remember_failure(key)
        return ratings

    def _remember_failure(self, key: str) -> None:
        self._failure.set(key, True)
