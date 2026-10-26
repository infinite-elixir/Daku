from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from daku.services.metadata import anilist
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import UpstreamError, make_client, request_cache_key


def test_parse_search_normalises_anime_fields(load_fixture: Callable[[str], object]) -> None:
    payload = load_fixture("anilist_search.json")

    candidates = anilist.parse_search(payload["data"])  # type: ignore[index]
    by_id = {candidate.source_id: candidate for candidate in candidates}

    assert set(by_id) == {"154587", "20954", "77777"}
    frieren = by_id["154587"]
    assert frieren.source == "anilist"
    assert frieren.kind == "anime"
    assert frieren.title == "Frieren: Beyond Journey's End"
    assert frieren.year == 2023
    assert frieren.id_mal == 52991
    assert frieren.format == "TV"
    assert frieren.episode_count == 28
    assert frieren.poster_url is not None

    movie = by_id["20954"]
    assert movie.id_mal is None
    assert movie.format == "MOVIE"
    assert movie.episode_count == 1

    ova = by_id["77777"]
    assert ova.year is None
    assert ova.format == "OVA"
    assert ova.poster_url is None


def test_parse_search_tolerates_missing_fields() -> None:
    payload = {
        "Page": {
            "media": [
                {"id": 1},
                {"id": None, "title": {"romaji": "No id"}},
                {"id": 2, "title": {"english": "No format"}},
                "not-a-dict",
            ]
        }
    }

    candidates = anilist.parse_search(payload)

    assert [candidate.source_id for candidate in candidates] == ["2"]
    candidate = candidates[0]
    assert candidate.format is None
    assert candidate.id_mal is None
    assert candidate.episode_count is None
    assert candidate.year is None
    assert candidate.poster_url is None


def test_parse_search_returns_empty_on_bad_payloads() -> None:
    assert anilist.parse_search(None) == []
    assert anilist.parse_search({}) == []
    assert anilist.parse_search({"Page": {}}) == []
    assert anilist.parse_search({"Page": {"media": None}}) == []


def test_strip_html_removes_tags_and_entities() -> None:
    value = "<p>A <b>mage</b> outlives her party.</p><br>She walks &amp; remembers."

    assert anilist.strip_html(value) == "A mage outlives her party.\nShe walks & remembers."
    assert anilist.strip_html(None) is None
    assert anilist.strip_html("   ") is None


def test_parse_detail_groups_relations_one_level(
    load_fixture: Callable[[str], object],
) -> None:
    payload = load_fixture("anilist_detail.json")

    detail = anilist.parse_detail(payload["data"])  # type: ignore[index]

    assert detail is not None
    assert detail.kind == "anime"
    assert detail.source_id == "154587"
    assert detail.id_mal == 52991
    assert detail.format == "TV"
    assert detail.episode_count == 28
    assert detail.year == 2023
    assert detail.genres == ["Adventure", "Drama", "Fantasy"]
    assert detail.studios == ["Madhouse"]
    assert detail.status == "FINISHED"
    assert detail.season == "FALL"
    assert detail.banner_url is not None
    assert detail.overview == "Frieren is an elf mage.\nShe walks & remembers."
    assert detail.synonyms == ["Frieren at the Funeral", "Funeral Frieren"]

    assert set(detail.relations) >= {"SEQUEL", "PREQUEL", "SIDE_STORY"}
    sequels = detail.relations["SEQUEL"]
    assert len(sequels) == 1  # deduplicated by (source_id, relation_type)
    assert sequels[0]["source_id"] == "170068"
    assert sequels[0]["format"] == "TV"
    assert detail.relations["PREQUEL"] == []
    assert detail.relations["SIDE_STORY"][0]["format"] == "OVA"
    assert detail.relations["ADAPTATION"][0]["format"] == "MANGA"

    # Relations carry one level only; the nested PREQUEL is never expanded.
    relation_ids = {item["source_id"] for group in detail.relations.values() for item in group}
    assert "1" not in relation_ids


def test_parse_detail_tolerates_missing_fields() -> None:
    assert anilist.parse_detail(None) is None
    assert anilist.parse_detail({}) is None
    assert anilist.parse_detail({"Media": {}}) is None

    detail = anilist.parse_detail({"Media": {"id": 5, "title": {"romaji": "Only a title"}}})

    assert detail is not None
    assert detail.format is None
    assert detail.episode_count is None
    assert detail.year is None
    assert detail.poster_url is None
    assert detail.overview is None
    assert detail.relations == {"SEQUEL": [], "PREQUEL": [], "SIDE_STORY": []}


@pytest.mark.asyncio
async def test_graphql_error_payload_raises_and_is_not_cached(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()
    body = anilist.search_body("frieren", 20)
    key = request_cache_key(anilist.ENDPOINT, body)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_graphql_error.json"))
        )
        async with make_client() as client:
            with pytest.raises(UpstreamError) as excinfo:
                await anilist.search(client, cache, "frieren", limit=20)

    assert "GraphQL error" in excinfo.value.detail
    assert cache.get_stale(key) is None
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_search_and_detail_use_hashed_cache_keys(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_search.json"))
        )
        async with make_client() as client:
            first = await anilist.search(client, cache, "frieren", limit=20)
            second = await anilist.search(client, cache, "frieren", limit=20)

    assert len(first) == 3
    assert [candidate.source_id for candidate in second] == [
        candidate.source_id for candidate in first
    ]
    assert len(cache) == 1


def test_streaming_episodes_strip_the_episode_prefix() -> None:
    payload = {
        "Media": {
            "id": 120377,
            "idMal": None,
            "format": "ONA",
            "episodes": 3,
            "title": {"romaji": "Cyberpunk: Edgerunners"},
            "streamingEpisodes": [
                {"title": "Episode 1 - Let You Down"},
                {"title": "Episode 2: Like a Boy"},
                {"title": "Episode 3 \u2013 Smooth Criminal"},
                {"title": "Episode 3 - Duplicate is ignored"},
                {"title": "Special"},
            ],
        }
    }

    detail = anilist.parse_detail(payload)

    assert detail is not None
    assert detail.streaming_episodes == [
        {"episode": 1, "title": "Let You Down"},
        {"episode": 2, "title": "Like a Boy"},
        {"episode": 3, "title": "Smooth Criminal"},
    ]
