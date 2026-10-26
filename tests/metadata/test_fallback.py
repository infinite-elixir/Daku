from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest
import respx

from daku.errors import AppError
from daku.services.metadata import cinemeta, imdb
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import make_client
from daku.services.metadata.search import get_detail, search_titles


def _imdb_url(query: str) -> str:
    return imdb.build_suggestion_url(query)


def _catalog_url(kind: str, query: str) -> str:
    return cinemeta.catalog_url(kind, query)


@pytest.mark.asyncio
async def test_imdb_failure_falls_back_to_cinemeta(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()
    query = "big buck bunny"
    catalog = load_fixture("cinemeta_catalog_movie.json")

    with respx.mock(assert_all_called=False) as mock:
        imdb_route = mock.get(_imdb_url(query)).mock(return_value=httpx.Response(500))
        mock.get(_catalog_url("movie", query)).mock(return_value=httpx.Response(200, json=catalog))
        async with make_client() as client:
            outcome = await search_titles(client, cache, query, kind="movie")

    assert imdb_route.call_count == 2  # one retry
    assert outcome.stale is False
    assert [result.source for result in outcome.results] == ["cinemeta", "cinemeta"]
    assert {result.source_id for result in outcome.results} == {"tt1234567", "tt15239678"}


@pytest.mark.asyncio
async def test_zero_imdb_results_falls_back_to_cinemeta(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()
    query = "big buck bunny"
    catalog = load_fixture("cinemeta_catalog_movie.json")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_imdb_url(query)).mock(return_value=httpx.Response(200, json={"d": []}))
        mock.get(_catalog_url("movie", query)).mock(return_value=httpx.Response(200, json=catalog))
        async with make_client() as client:
            outcome = await search_titles(client, cache, query, kind="movie")

    assert outcome.stale is False
    assert len(outcome.results) == 2


@pytest.mark.asyncio
async def test_stale_cache_is_served_when_both_upstreams_fail(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()
    query = "big buck bunny"
    catalog = load_fixture("cinemeta_catalog_movie.json")
    cache.set(
        _catalog_url("movie", query),
        catalog,
        stored_at=time.monotonic() - 7200,
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_imdb_url(query)).mock(return_value=httpx.Response(503))
        mock.get(_catalog_url("movie", query)).mock(return_value=httpx.Response(503))
        async with make_client() as client:
            outcome = await search_titles(client, cache, query, kind="movie")

    assert outcome.stale is True
    assert {result.source_id for result in outcome.results} == {"tt1234567", "tt15239678"}


@pytest.mark.asyncio
async def test_both_upstreams_fail_without_cache() -> None:
    cache = TTLCache()
    query = "nothing here"

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_imdb_url(query)).mock(return_value=httpx.Response(500))
        mock.get(_catalog_url("movie", query)).mock(return_value=httpx.Response(500))
        async with make_client() as client:
            with pytest.raises(AppError) as excinfo:
                await search_titles(client, cache, query, kind="movie")

    assert excinfo.value.code == "SEARCH_UNAVAILABLE"
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_fresh_cache_avoids_network(
    load_fixture: Callable[[str], object],
) -> None:
    cache = TTLCache()
    query = "big buck bunny"
    cache.set(_imdb_url(query), load_fixture("imdb_suggestions.json"))

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(_imdb_url(query)).mock(return_value=httpx.Response(500))
        async with make_client() as client:
            outcome = await search_titles(client, cache, query, kind="movie")

    assert route.call_count == 0
    assert outcome.stale is False
    assert outcome.results[0].source_id == "tt1234567"


@pytest.mark.asyncio
async def test_detail_serves_stale_meta(load_fixture: Callable[[str], object]) -> None:
    cache = TTLCache()
    url = cinemeta.meta_url("series", "tt0944947")
    cache.set(url, load_fixture("cinemeta_meta_series.json"), stored_at=time.monotonic() - 7200)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(500))
        async with make_client() as client:
            detail, stale = await get_detail(client, cache, "series", "tt0944947")

    assert stale is True
    assert detail.episodes[0].label == "S01E01"
