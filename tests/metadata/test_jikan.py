from __future__ import annotations

import time
from typing import Any

import httpx
import respx

from daku.services.metadata import jikan
from daku.services.metadata.cache import TTLCache


def _page(titles: list[Any], *, last: int) -> dict[str, Any]:
    data: list[Any] = []
    for title in titles:
        if isinstance(title, str):
            data.append(
                {
                    "mal_id": len(data) + 1,
                    "title": title,
                    "aired": "2023-09-29",
                    "filler": False,
                    "recap": False,
                }
            )
        else:
            data.append(title)
    return {"pagination": {"last_visible_page": last}, "data": data}


def test_parse_assigns_positional_numbers_and_skips_malformed_rows() -> None:
    payload = {
        "pagination": {"last_visible_page": 4},
        "data": [
            {"title": "First", "aired": "2023-01-01"},
            "not a row",
            {"title": "  ", "aired": None},
            {"title": "Fourth", "aired": "2023-01-04"},
        ],
    }

    episodes, last = jikan.parse_episodes(payload)

    assert last == 4
    assert [episode.number for episode in episodes] == [1, 2, 3]
    assert episodes[0].title == "First"
    assert episodes[0].air_date == "2023-01-01"
    assert episodes[1].title is None


async def test_fetch_follows_pagination_and_stops_at_provider_count() -> None:
    jikan.clear_cache()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.jikan.moe/v4/anime/42/episodes?page=1").mock(
            return_value=httpx.Response(200, json=_page(["One", "Two"], last=2))
        )
        mock.get("https://api.jikan.moe/v4/anime/42/episodes?page=2").mock(
            return_value=httpx.Response(200, json=_page(["Three", "Four"], last=2))
        )
        async with httpx.AsyncClient() as client:
            episodes = await jikan.fetch_episode_titles(
                client,
                TTLCache(ttl_seconds=1),
                42,
                episode_count=200,
            )

    assert [(episode.number, episode.title) for episode in episodes] == [
        (1, "One"),
        (2, "Two"),
        (3, "Three"),
        (4, "Four"),
    ]


async def test_a_failed_request_serves_the_stale_list() -> None:
    jikan.clear_cache()
    stale = [jikan.JikanEpisode(number=1, title="Cached", air_date=None)]
    jikan._episode_cache.set(
        "jikan:episodes:77",
        stale,
        stored_at=time.monotonic() - jikan.CACHE_TTL - 1,
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.jikan.moe/v4/anime/77/episodes?page=1").mock(
            return_value=httpx.Response(429)
        )
        async with httpx.AsyncClient() as client:
            episodes = await jikan.fetch_episode_titles(client, TTLCache(), 77)

    assert episodes == stale


async def test_errors_never_escape() -> None:
    jikan.clear_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.jikan.moe/v4/anime/88/episodes?page=1").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            assert await jikan.fetch_episode_titles(client, TTLCache(), 88) == []
