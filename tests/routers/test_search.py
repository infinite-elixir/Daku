from __future__ import annotations

from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.services.metadata import cinemeta, imdb


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def test_search_shape(settings: Settings, load_fixture: Callable[[str], object]) -> None:
    query = "big buck bunny"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=load_fixture("imdb_suggestions.json"))
        )
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == query
    assert payload["kind"] is None
    assert payload["stale"] is False
    results = payload["results"]
    assert len(results) == 5
    assert set(results[0]) == {
        "source",
        "source_id",
        "kind",
        "title",
        "year",
        "poster_url",
    }
    assert results[0]["source"] == "imdb"
    assert results[0]["source_id"] == "tt1234567"


def test_search_kind_filter_and_limit(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    query = "game of thrones"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=load_fixture("imdb_suggestions.json"))
        )
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query, "kind": "series", "limit": 1})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["kind"] == "series"


def test_search_deduplicates_source_id_and_kind(settings: Settings) -> None:
    query = "duplicate"
    duplicate = {
        "d": [
            {"id": "tt1234567", "l": "Big Buck Bunny", "qid": "movie"},
            {"id": "tt1234567", "l": "Big Buck Bunny", "qid": "movie"},
        ]
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=duplicate)
        )
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


def test_search_falls_back_to_cinemeta(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    query = "big buck bunny"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(return_value=httpx.Response(500))
        mock.get(cinemeta.catalog_url("movie", query)).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_catalog_movie.json"))
        )
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query, "kind": "movie"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["source"] == "cinemeta"


def test_search_empty_query_is_422(settings: Settings) -> None:
    with _app_client(settings) as client:
        assert client.get("/api/search", params={"q": ""}).status_code == 422
        assert client.get("/api/search", params={"q": "   "}).status_code == 422
        assert client.get("/api/search").status_code == 422


def test_search_surfaces_upstream_error_without_cache(settings: Settings) -> None:
    query = "down"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(return_value=httpx.Response(500))
        mock.get(cinemeta.catalog_url("movie", query)).mock(return_value=httpx.Response(500))
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query, "kind": "movie"})

    assert response.status_code == 502
    assert response.json()["code"] == "SEARCH_UNAVAILABLE"
