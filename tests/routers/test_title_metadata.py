from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daku.app import create_app
from daku.config import Settings
from daku.models import Title
from daku.services.metadata import cinemeta
from daku.services.metadata.ratings import OMDB_URL

RICH_META: dict[str, Any] = {
    "meta": {
        "id": "tt15239678",
        "type": "movie",
        "name": "Dune: Part Two",
        "releaseInfo": "2024",
        "poster": "https://images.example/dune.jpg",
        "background": "https://images.example/dune-banner.jpg",
        "description": "Paul Atreides unites with the Fremen.",
        "imdbRating": "8.5",
        "runtime": "167 min",
        "genres": ["Action", "Adventure", "Drama"],
        "cast": ["Timothée Chalamet", "Zendaya", "Rebecca Ferguson"],
        "director": ["Denis Villeneuve"],
        "writer": ["Denis Villeneuve", "Jon Spaihts"],
    }
}

OMDB_PAYLOAD: dict[str, Any] = {
    "Response": "True",
    "imdbRating": "7.1",
    "Ratings": [
        {"Source": "Rotten Tomatoes", "Value": "92%"},
        {"Source": "Metacritic", "Value": "79/100"},
    ],
}


def _client(settings: Settings, *, omdb_key: str = "") -> TestClient:
    return TestClient(
        create_app(settings.model_copy(update={"omdb_api_key": omdb_key}), configure_logs=False)
    )


def _add_title(client: TestClient, imdb_id: str = "tt15239678") -> dict[str, Any]:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", imdb_id)).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        response = client.post(
            "/api/titles",
            json={"source": "imdb", "source_id": imdb_id, "kind": "movie"},
        )
    assert response.status_code == 201
    return response.json()


def _session(client: TestClient) -> Session:
    return client.app.state.session_factory()  # type: ignore[attr-defined]


def test_metadata_endpoint_renders_cinemeta_without_a_key(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        omdb_route = mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=OMDB_PAYLOAD))
        with _client(settings) as client:
            title = _add_title(client)
            response = client.get(f"/api/titles/{title['id']}/metadata")

    assert response.status_code == 200
    payload = response.json()
    assert payload["imdb_rating"] == 8.5
    assert payload["banner_url"] == "https://images.example/dune-banner.jpg"
    assert payload["rt_rating"] is None
    assert payload["metacritic_rating"] is None
    assert payload["runtime_minutes"] == 167
    assert payload["genres"] == ["Action", "Adventure", "Drama"]
    assert payload["cast"][0] == "Timothée Chalamet"
    assert payload["directors"] == ["Denis Villeneuve"]
    assert payload["writers"] == ["Denis Villeneuve", "Jon Spaihts"]
    assert payload["sources"] == ["cinemeta"]
    assert payload["stale"] is False
    assert omdb_route.called is False


def test_metadata_endpoint_adds_rt_and_metacritic_with_a_key(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=OMDB_PAYLOAD))
        with _client(settings, omdb_key="secret-key") as client:
            title = _add_title(client)
            response = client.get(f"/api/titles/{title['id']}/metadata")

    payload = response.json()
    assert payload["rt_rating"] == 92
    assert payload["metacritic_rating"] == 79
    # Cinemeta's IMDb rating wins over OMDb's disagreement.
    assert payload["imdb_rating"] == 8.5
    assert payload["sources"] == ["cinemeta", "omdb"]
    assert "secret-key" not in response.text


def test_metadata_endpoint_is_read_only(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=OMDB_PAYLOAD))
        with _client(settings, omdb_key="secret-key") as client:
            title = _add_title(client)
            session = _session(client)
            try:
                before = session.get(Title, title["id"])
                assert before is not None
                snapshot = (before.metadata_synced_at, before.metadata_source, before.overview)
                count_before = int(session.scalar(select(func.count()).select_from(Title)) or 0)
            finally:
                session.close()

            first = client.get(f"/api/titles/{title['id']}/metadata")
            second = client.get(f"/api/titles/{title['id']}/metadata")

            session = _session(client)
            try:
                after = session.get(Title, title["id"])
                assert after is not None
                assert (after.metadata_synced_at, after.metadata_source, after.overview) == snapshot
                count_after = int(session.scalar(select(func.count()).select_from(Title)) or 0)
            finally:
                session.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert count_before == count_after == 1


def test_preview_reports_the_same_rich_fields(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=OMDB_PAYLOAD))
        with _client(settings, omdb_key="secret-key") as client:
            title = _add_title(client)
            metadata = client.get(f"/api/titles/{title['id']}/metadata").json()
            preview = client.get(
                "/api/metadata/preview",
                params={"source": "imdb", "source_id": "tt15239678", "kind": "movie"},
            ).json()

    for key in (
        "imdb_rating",
        "rt_rating",
        "metacritic_rating",
        "runtime_minutes",
        "genres",
        "cast",
        "directors",
        "writers",
        "stale",
        "sources",
    ):
        assert preview[key] == metadata[key], key


def test_metadata_endpoint_survives_metadata_outage(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        mock.get(OMDB_URL).mock(return_value=httpx.Response(200, json=OMDB_PAYLOAD))
        with _client(settings, omdb_key="secret-key") as client:
            title = _add_title(client)

        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(return_value=httpx.Response(503))
        with _client(settings, omdb_key="secret-key") as client:
            response = client.get(f"/api/titles/{title['id']}/metadata")

    assert response.status_code == 200
    payload = response.json()
    assert payload["imdb_rating"] is None
    assert payload["stale"] is True
    assert payload["sources"] == []


def test_invalid_omdb_key_still_renders_and_shows_in_doctor(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt15239678")).mock(
            return_value=httpx.Response(200, json=RICH_META)
        )
        mock.get(OMDB_URL).mock(return_value=httpx.Response(401))
        with _client(settings, omdb_key="bad-key") as client:
            title = _add_title(client)
            response = client.get(f"/api/titles/{title['id']}/metadata")
            doctor = client.get("/api/system/doctor")

    assert response.status_code == 200
    assert response.json()["imdb_rating"] == 8.5
    checks = {check["name"]: check for check in doctor.json()["checks"]}
    assert checks["omdb"]["detail"] == "invalid-key"
    assert checks["omdb"]["status"] == "error"


def test_doctor_reports_configured_and_unconfigured(settings: Settings) -> None:
    with _client(settings) as client:
        unconfigured = client.get("/api/system/doctor").json()
    with _client(settings, omdb_key="secret-key") as client:
        configured = client.get("/api/system/doctor").json()

    unconfigured_checks = {check["name"]: check for check in unconfigured["checks"]}
    configured_checks = {check["name"]: check for check in configured["checks"]}
    assert unconfigured_checks["omdb"]["detail"] == "unconfigured"
    assert unconfigured_checks["omdb"]["status"] == "not_configured"
    assert configured_checks["omdb"]["detail"] == "configured"
    assert configured_checks["omdb"]["status"] == "ok"


def test_metadata_endpoint_404_for_unknown_title(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/api/titles/does-not-exist/metadata")

    assert response.status_code == 404
