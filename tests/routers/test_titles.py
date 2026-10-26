from __future__ import annotations

from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.services.metadata import cinemeta


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def test_series_detail_returns_ordered_episodes(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    url = cinemeta.meta_url("series", "tt0944947")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        with _app_client(settings) as client:
            response = client.get("/api/metadata/series/tt0944947")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cinemeta"
    assert payload["source_id"] == "tt0944947"
    assert payload["kind"] == "series"
    assert payload["stale"] is False
    assert [episode["label"] for episode in payload["episodes"]] == [
        "S01E01",
        "S01E02",
        "S02E01",
    ]


def test_detail_rejects_non_imdb_id(settings: Settings) -> None:
    with _app_client(settings) as client:
        response = client.get("/api/metadata/series/not-an-id")

    assert response.status_code == 422


def test_detail_returns_502_when_unavailable(settings: Settings) -> None:
    url = cinemeta.meta_url("movie", "tt1234567")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(500))
        with _app_client(settings) as client:
            response = client.get("/api/metadata/movie/tt1234567")

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"
