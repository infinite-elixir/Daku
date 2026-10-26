from __future__ import annotations

from typing import Any

import httpx
import respx

from daku.services.metadata.client import make_client
from daku.services.metadata.ratings import (
    OMDB_URL,
    STATUS_CONFIGURED,
    STATUS_INVALID_KEY,
    STATUS_UNCONFIGURED,
    OmdbEnricher,
    parse_ratings,
)

FULL_PAYLOAD: dict[str, Any] = {
    "Title": "Dune: Part Two",
    "Response": "True",
    "imdbRating": "8.5",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.5/10"},
        {"Source": "Rotten Tomatoes", "Value": "92%"},
        {"Source": "Metacritic", "Value": "79/100"},
    ],
}


def test_parse_ratings_extracts_every_source() -> None:
    ratings = parse_ratings(FULL_PAYLOAD)

    assert ratings.imdb_rating == 8.5
    assert ratings.rt_rating == 92
    assert ratings.metacritic_rating == 79


def test_parse_ratings_tolerates_malformed_values() -> None:
    ratings = parse_ratings(
        {
            "Response": "True",
            "imdbRating": "N/A",
            "Ratings": [
                {"Source": "Rotten Tomatoes", "Value": "N/A"},
                {"Source": "Metacritic", "Value": "not a score"},
                {"Source": "Rotten Tomatoes", "Value": 150},
                "not-a-dict",
            ],
        }
    )

    assert ratings.imdb_rating is None
    assert ratings.rt_rating is None
    assert ratings.metacritic_rating is None
    assert ratings.any() is False


def test_parse_ratings_handles_not_found() -> None:
    assert parse_ratings({"Response": "False", "Error": "Movie not found!"}).any() is False
    assert parse_ratings(None).any() is False


async def test_no_key_makes_no_request() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=FULL_PAYLOAD))
            ratings = await enricher.ratings(client, "tt15239678", "")

    assert ratings.any() is False
    assert route.called is False
    assert enricher.status("") == STATUS_UNCONFIGURED


async def test_enrichment_fetches_and_caches_for_a_day() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=FULL_PAYLOAD))
            first = await enricher.ratings(client, "tt15239678", "secret-key")
            second = await enricher.ratings(client, "tt15239678", "secret-key")

    assert first.rt_rating == 92
    assert second.metacritic_rating == 79
    assert route.call_count == 1
    assert enricher.status("secret-key") == STATUS_CONFIGURED


async def test_not_found_is_cached_briefly_not_for_a_day() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(
                return_value=httpx.Response(200, json={"Response": "False", "Error": "not found"})
            )
            first = await enricher.ratings(client, "tt0000001", "secret-key")
            second = await enricher.ratings(client, "tt0000001", "secret-key")

    assert first.any() is False
    assert second.any() is False
    assert route.call_count == 1


async def test_401_disables_enrichment_for_the_process() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(return_value=httpx.Response(401))
            first = await enricher.ratings(client, "tt15239678", "bad-key")
            second = await enricher.ratings(client, "tt15239678", "bad-key")

    assert first.any() is False
    assert second.any() is False
    assert route.call_count == 1
    assert enricher.disabled is True
    assert enricher.status("bad-key") == STATUS_INVALID_KEY


async def test_invalid_key_body_disables_enrichment() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(OMDB_URL).mock(
                return_value=httpx.Response(
                    200, json={"Response": "False", "Error": "Invalid API key!"}
                )
            )
            ratings = await enricher.ratings(client, "tt15239678", "bad-key")

    assert ratings.any() is False
    assert enricher.disabled is True


async def test_429_backs_off_and_caches_the_failure() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(return_value=httpx.Response(429))
            first = await enricher.ratings(client, "tt15239678", "secret-key")
            second = await enricher.ratings(client, "tt15239678", "secret-key")

    assert first.any() is False
    assert second.any() is False
    # One initial attempt plus the single retry; the cached failure stops the second call.
    assert route.call_count == 2


async def test_network_timeout_returns_empty() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(OMDB_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
            ratings = await enricher.ratings(client, "tt15239678", "secret-key")

    assert ratings.any() is False


async def test_requests_never_share_a_cache_entry_across_ids() -> None:
    enricher = OmdbEnricher()
    async with make_client() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=FULL_PAYLOAD))
            await enricher.ratings(client, "tt15239678", "secret-key")
            await enricher.ratings(client, "tt0944947", "secret-key")

    assert route.call_count == 2
