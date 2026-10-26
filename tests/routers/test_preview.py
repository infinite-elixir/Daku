from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daku.app import create_app
from daku.config import Settings
from daku.models import Title
from daku.services.metadata import anilist, cinemeta

MOVIE_META: dict[str, Any] = {
    "meta": {
        "id": "tt1234567",
        "type": "movie",
        "name": "Big Buck Bunny",
        "releaseInfo": "2008",
        "poster": "https://images.example/bbb.jpg",
        "description": "A large rabbit deals with three bullying rodents.",
    }
}


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _session(client: TestClient) -> Session:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory()


def _preview(
    client: TestClient,
    *,
    source: str,
    source_id: str,
    kind: str,
) -> Any:
    return client.get(
        "/api/metadata/preview",
        params={"source": source, "source_id": source_id, "kind": kind},
    )


def test_movie_preview_is_normalised_and_writes_nothing(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt1234567")).mock(
            return_value=httpx.Response(200, json=MOVIE_META)
        )
        with _app_client(settings) as client:
            response = _preview(client, source="imdb", source_id="tt1234567", kind="movie")
            session = _session(client)
            try:
                titles = _count(session, Title)
            finally:
                session.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Big Buck Bunny"
    assert payload["year"] == 2008
    assert payload["kind"] == "movie"
    assert payload["imdb_id"] == "tt1234567"
    assert payload["overview"]
    assert payload["poster_url"] == "https://images.example/bbb.jpg"
    assert payload["library_title_id"] is None
    assert payload["stale"] is False
    assert payload["episodes"] == []
    assert payload["episode_count"] == 0
    assert titles == 0


def test_series_preview_lists_every_episode(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt0944947")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        with _app_client(settings) as client:
            response = _preview(client, source="cinemeta", source_id="tt0944947", kind="series")

    assert response.status_code == 200
    payload = response.json()
    assert payload["library_title_id"] is None
    assert payload["episode_count"] == 3
    assert [episode["label"] for episode in payload["episodes"]] == [
        "S01E01",
        "S01E02",
        "S02E01",
    ]
    assert set(payload["episodes"][0]) == {
        "id",
        "season",
        "episode",
        "label",
        "absolute",
        "name",
        "air_date",
        "status",
    }
    assert payload["episodes"][0]["name"] == "Winter Is Coming"
    assert payload["episodes"][0]["status"] == "missing"
    assert payload["episodes"][0]["id"].startswith("preview:")


def test_anime_preview_derives_absolute_numbers(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        with _app_client(settings) as client:
            response = _preview(client, source="anilist", source_id="154587", kind="anime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Frieren: Beyond Journey's End"
    assert payload["kind"] == "anime"
    assert payload["anilist_id"] == 154587
    assert payload["imdb_id"] is None
    assert payload["mal_id"] == 52991
    assert len(payload["episodes"]) == 28
    assert payload["episodes"][0]["absolute"] == 1
    assert payload["episodes"][0]["season"] == 1


def test_preview_matches_existing_library_title(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt0944947")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        with _app_client(settings) as client:
            added = client.post(
                "/api/titles",
                json={"source": "imdb", "source_id": "tt0944947", "kind": "series"},
            )
            assert added.status_code == 201
            response = _preview(client, source="imdb", source_id="tt0944947", kind="series")

    assert response.status_code == 200
    payload = response.json()
    assert payload["library_title_id"] == added.json()["id"]
    assert payload["id"] == added.json()["id"]
    assert payload["availability"] == "none"


def test_preview_unknown_id_does_not_write(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt9999999")).mock(return_value=httpx.Response(404))
        with _app_client(settings) as client:
            response = _preview(client, source="imdb", source_id="tt9999999", kind="movie")
            session = _session(client)
            try:
                titles = _count(session, Title)
            finally:
                session.close()

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"
    assert titles == 0


def test_preview_metadata_outage_returns_502(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt0944947")).mock(return_value=httpx.Response(503))
        with _app_client(settings) as client:
            response = _preview(client, source="imdb", source_id="tt0944947", kind="series")

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"


def test_preview_rejects_mismatched_source(settings: Settings) -> None:
    with _app_client(settings) as client:
        response = _preview(client, source="imdb", source_id="154587", kind="anime")

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TITLE_SOURCE"


def test_add_accepts_metadata_source(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt1234567")).mock(
            return_value=httpx.Response(200, json=MOVIE_META)
        )
        with _app_client(settings) as client:
            response = client.post(
                "/api/titles",
                json={
                    "source": "imdb",
                    "source_id": "tt1234567",
                    "kind": "movie",
                    "metadata_source": "cinemeta",
                },
            )
            second = client.post(
                "/api/titles",
                json={
                    "source": "imdb",
                    "source_id": "tt1234567",
                    "kind": "movie",
                    "metadata_source": "cinemeta",
                },
            )

    assert response.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == response.json()["id"]


def test_add_rejects_unknown_metadata_source(settings: Settings) -> None:
    with _app_client(settings) as client:
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt1234567",
                "kind": "movie",
                "metadata_source": "tmdb",
            },
        )

    assert response.status_code == 422
