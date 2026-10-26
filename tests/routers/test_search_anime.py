from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.services.metadata import anilist, imdb
from daku.services.metadata.client import request_cache_key


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def _mock_anilist(mock: respx.MockRouter, fixture: object) -> respx.Route:
    return mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(200, json=fixture))


def test_anime_search_returns_anilist_results(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    query = "frieren"
    with respx.mock(assert_all_called=False) as mock:
        _mock_anilist(mock, load_fixture("anilist_search.json"))
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query, "kind": "anime"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "anime"
    assert payload["format"] is None
    assert payload["stale"] is False
    results = payload["results"]
    assert len(results) == 3

    first = results[0]
    assert set(first) == {
        "source",
        "source_id",
        "kind",
        "title",
        "year",
        "poster_url",
        "id_mal",
        "format",
        "episode_count",
    }
    assert first["source"] == "anilist"
    assert first["source_id"] == "154587"
    assert first["kind"] == "anime"
    assert first["id_mal"] == 52991
    assert first["format"] == "TV"
    assert first["episode_count"] == 28
    assert first["year"] == 2023


def test_anime_search_filters_by_format(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_anilist(mock, load_fixture("anilist_search.json"))
        with _app_client(settings) as client:
            response = client.get(
                "/api/search", params={"q": "frieren", "kind": "anime", "format": "OVA"}
            )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["source_id"] == "77777"
    assert results[0]["format"] == "OVA"


def test_format_without_anime_kind_is_422(settings: Settings) -> None:
    with _app_client(settings) as client:
        assert client.get("/api/search", params={"q": "x", "format": "TV"}).status_code == 422
        assert (
            client.get(
                "/api/search", params={"q": "x", "kind": "movie", "format": "TV"}
            ).status_code
            == 422
        )


def test_anime_search_does_not_call_imdb(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    query = "frieren"
    with respx.mock(assert_all_called=False) as mock:
        imdb_route = mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json={"d": []})
        )
        _mock_anilist(mock, load_fixture("anilist_search.json"))
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": query, "kind": "anime"})

    assert response.status_code == 200
    assert imdb_route.call_count == 0


def test_anime_search_serves_stale_cache(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    query = "frieren"
    body = anilist.search_body(query, 20)
    key = request_cache_key(anilist.ENDPOINT, body)

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(500))
        with _app_client(settings) as client:
            client.app.state.metadata_cache.set(
                key,
                load_fixture("anilist_search.json"),
                stored_at=time.monotonic() - 7200,
            )
            response = client.get("/api/search", params={"q": query, "kind": "anime"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stale"] is True
    assert len(payload["results"]) == 3


def test_anime_search_returns_502_without_cache(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})
        )
        with _app_client(settings) as client:
            response = client.get("/api/search", params={"q": "x", "kind": "anime"})

    assert response.status_code == 502
    assert response.json()["code"] == "SEARCH_UNAVAILABLE"


def test_anime_detail_returns_grouped_relations(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_anilist(mock, load_fixture("anilist_detail.json"))
        with _app_client(settings) as client:
            response = client.get("/api/metadata/anime/154587")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "anilist"
    assert payload["source_id"] == "154587"
    assert payload["kind"] == "anime"
    assert payload["stale"] is False
    assert payload["format"] == "TV"
    assert payload["episode_count"] == 28
    assert payload["overview"] == "Frieren is an elf mage.\nShe walks & remembers."
    assert "relations" in payload
    assert payload["relations"]["SEQUEL"][0]["source_id"] == "170068"
    assert payload["relations"]["SIDE_STORY"][0]["source_id"] == "77777"
    assert payload["relations"]["PREQUEL"] == []


def test_anime_detail_rejects_non_numeric_id(settings: Settings) -> None:
    with _app_client(settings) as client:
        assert client.get("/api/metadata/anime/not-an-id").status_code == 422
        assert client.get("/api/metadata/anime/0").status_code == 422


def test_anime_detail_returns_502_when_unavailable(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(500))
        with _app_client(settings) as client:
            response = client.get("/api/metadata/anime/154587")

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"
