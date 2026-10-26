from __future__ import annotations

from typing import Any

import httpx
import pytest

from daku.services.metadata import jikan
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.models import EpisodeRef, TitleDetail
from daku.services.metadata.resolve import enrich_anime_episode_names


@pytest.fixture(autouse=True)
def _clear_jikan_cache() -> None:
    jikan.clear_cache()


async def test_tvdb_specials_keep_their_names_and_stay_in_season_zero() -> None:
    detail = TitleDetail(
        source="anilist",
        source_id="1",
        kind="anime",
        title="With Specials",
        episodes=[
            EpisodeRef(season=0, episode=1),
            EpisodeRef(season=1, episode=1),
        ],
        episode_count=2,
    )
    tvdb_refs = [
        EpisodeRef(season=0, episode=1, title="The Special", air_date="2022-01-01"),
        EpisodeRef(season=1, episode=1, title="The Pilot", air_date="2022-01-08"),
    ]

    async with httpx.AsyncClient() as client:
        result = await enrich_anime_episode_names(
            client,
            TTLCache(),
            detail,
            tvdb_refs=tvdb_refs,
        )

    assert result["source"] == "tvdb"
    assert [(ref.season, ref.episode, ref.title) for ref in detail.episodes] == [
        (0, 1, "The Special"),
        (1, 1, "The Pilot"),
    ]
    assert detail.episodes[0].air_date == "2022-01-01"


async def test_tvdb_names_win_over_jikan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(*_args: Any, **_kwargs: Any) -> list[jikan.JikanEpisode]:
        return [jikan.JikanEpisode(number=1, title="Jikan Name", air_date=None)]

    monkeypatch.setattr(jikan, "fetch_episode_titles", fake_fetch)
    detail = TitleDetail(
        source="anilist",
        source_id="2",
        kind="anime",
        title="Precedence",
        id_mal=500,
        episodes=[EpisodeRef(season=1, episode=1)],
        episode_count=1,
    )

    async with httpx.AsyncClient() as client:
        await enrich_anime_episode_names(
            client,
            TTLCache(),
            detail,
            tvdb_refs=[EpisodeRef(season=1, episode=1, title="TVDB Name")],
        )

    assert detail.episodes[0].title == "TVDB Name"


async def test_a_source_without_names_never_erases_an_existing_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(*_args: Any, **_kwargs: Any) -> list[jikan.JikanEpisode]:
        return []

    monkeypatch.setattr(jikan, "fetch_episode_titles", fake_fetch)
    detail = TitleDetail(
        source="anilist",
        source_id="3",
        kind="anime",
        title="Keep Names",
        id_mal=600,
        episodes=[EpisodeRef(season=1, episode=1, title="Keep me")],
        episode_count=1,
    )

    async with httpx.AsyncClient() as client:
        await enrich_anime_episode_names(client, TTLCache(), detail)

    assert detail.episodes[0].title == "Keep me"
