from __future__ import annotations

import httpx
import pytest
import respx

from daku.errors import AppError
from daku.services.metadata import anilist
from daku.services.metadata.anime_identity import validate_anilist_anime
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import make_client

MANGA_ID = 31706
MAL_ONLY_ID = 61469


def _manga_media() -> dict[str, object]:
    return {
        "id": MANGA_ID,
        "type": "MANGA",
        "format": "MANGA",
        "episodes": None,
        "seasonYear": None,
        "title": {"romaji": "JoJo no Kimyou na Bouken: Steel Ball Run"},
    }


def _anime_media(anilist_id: int = 190327) -> dict[str, object]:
    return {
        "id": anilist_id,
        "idMal": 61469,
        "type": "ANIME",
        "format": "ONA",
        "episodes": 1,
        "seasonYear": 2026,
        "title": {
            "romaji": "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE",
            "english": "STEEL BALL RUN JoJo's Bizarre Adventure 1st STAGE",
        },
    }


@pytest.mark.asyncio
async def test_assigning_a_manga_id_is_rejected_with_the_row_detail() -> None:
    cache = TTLCache()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json={"data": {"Media": None}}),
                httpx.Response(200, json={"data": {"Media": _manga_media()}}),
            ]
        )
        async with make_client() as client:
            with pytest.raises(AppError) as excinfo:
                await validate_anilist_anime(client, cache, MANGA_ID)

    error = excinfo.value
    assert error.code == "ANILIST_NOT_ANIME"
    assert error.status_code == 422
    assert error.detail["anilist_id"] == MANGA_ID
    assert error.detail["type"] == "MANGA"
    assert error.detail["format"] == "MANGA"
    assert "Steel Ball Run" in str(error.detail["title"])


@pytest.mark.asyncio
async def test_a_myanimelist_id_suggests_its_anilist_identity() -> None:
    cache = TTLCache()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json={"data": {"Media": None}}),
                httpx.Response(200, json={"data": {"Media": None}}),
                httpx.Response(200, json={"data": {"Media": _anime_media()}}),
            ]
        )
        async with make_client() as client:
            with pytest.raises(AppError) as excinfo:
                await validate_anilist_anime(client, cache, MAL_ONLY_ID)

    error = excinfo.value
    assert error.code == "ANILIST_IS_MAL"
    assert error.status_code == 422
    assert error.detail["mal_id"] == MAL_ONLY_ID
    assert error.detail["suggested_anilist_id"] == 190327


@pytest.mark.asyncio
async def test_an_unreachable_anilist_returns_the_actionable_outage() -> None:
    cache = TTLCache()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(503))
        async with make_client() as client:
            with pytest.raises(AppError) as excinfo:
                await validate_anilist_anime(client, cache, 190327)

    assert excinfo.value.code == "METADATA_UNAVAILABLE"
    assert excinfo.value.status_code == 502
