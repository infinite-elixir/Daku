"""Validate that a manual AniList id really is an anime entry (T-052).

AniList answers ``Media(id: N, type: ANIME)`` with no media for a manga id, which
used to look like a temporary outage. Manual identity edits must not persist a
manga id, so validation probes the unfiltered entry before the override is written.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from daku.errors import AppError
from daku.services.metadata import anilist
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import UpstreamError
from daku.services.metadata.models import TitleDetail
from daku.services.metadata.search import get_anime_detail

ANIME_FORMATS = frozenset({"TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT"})


def _title(media: dict[str, Any]) -> str | None:
    value = media.get("title")
    if not isinstance(value, dict):
        return None
    for key in ("english", "romaji", "native"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
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


async def validate_anilist_anime(
    client: httpx.AsyncClient,
    cache: TTLCache,
    anilist_id: int,
) -> tuple[TitleDetail, bool]:
    """Return the AniList detail, or raise a stable 422/502 ``AppError``."""

    try:
        return await get_anime_detail(client, cache, anilist_id)
    except AppError as original:
        # A manga id and a missing id both fail the typed lookup. Ask the
        # unfiltered endpoint so the user gets "that is a manga", not "outage".
        try:
            media = await anilist.probe_media(
                client,
                cache,
                anilist_id,
                deadline=time.monotonic() + 5.0,
            )
        except (UpstreamError, AppError):
            raise original from None

        if media is None:
            # A MyAnimeList id typed into the AniList field points at nothing on
            # AniList. Resolve it through ``Media(idMal:)`` and tell the user the
            # AniList id they probably meant.
            try:
                mal_media = await anilist.probe_media_by_mal(
                    client,
                    cache,
                    anilist_id,
                    deadline=time.monotonic() + 5.0,
                )
            except (UpstreamError, AppError):
                mal_media = None
            if mal_media is not None:
                resolved_id = _as_int(mal_media.get("id"))
                raise AppError(
                    code="ANILIST_IS_MAL",
                    message=(
                        f"{anilist_id} is a MyAnimeList id; "
                        f"its AniList id is {resolved_id or 'unknown'}"
                    ),
                    status_code=422,
                    detail={
                        "anilist_id": anilist_id,
                        "suggested_anilist_id": resolved_id,
                        "mal_id": anilist_id,
                        "type": str(mal_media.get("type") or "").upper() or None,
                        "format": mal_media.get("format"),
                        "title": _title(mal_media),
                    },
                ) from original
            raise AppError(
                code="ANILIST_NOT_FOUND",
                message=f"AniList id {anilist_id} does not exist",
                status_code=422,
                detail={"anilist_id": anilist_id},
            ) from original

        media_type = str(media.get("type") or "").upper()
        if media_type != "ANIME":
            raise AppError(
                code="ANILIST_NOT_ANIME",
                message=(
                    f"AniList id {anilist_id} is a {media_type or 'non-anime'} entry, not an anime"
                ),
                status_code=422,
                detail={
                    "anilist_id": anilist_id,
                    "type": media_type or None,
                    "format": media.get("format"),
                    "title": _title(media),
                    "episodes": media.get("episodes"),
                    "season_year": media.get("seasonYear"),
                },
            ) from original

        # It is an anime, but the typed lookup failed for another reason (for
        # example a malformed detail payload). Surface the original outage.
        raise original from None


__all__ = ["ANIME_FORMATS", "validate_anilist_anime"]
