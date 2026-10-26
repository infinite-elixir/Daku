from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from daku.repositories import titles
from daku.services.metadata import mapping


def _ingest(client: TestClient, payload: dict[str, Any]) -> mapping.IngestReport:
    return mapping.ingest_payload(client.app.state.engine, payload)


def test_system_mappings_reports_coverage(
    client: TestClient, mapping_payload: dict[str, Any]
) -> None:
    _ingest(client, mapping_payload)

    response = client.get("/api/system/mappings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["stale"] is False
    assert payload["row_count"] > 0
    assert payload["coverage"] == {
        "imdb_show": 1,
        "anilist": 1,
        "mal": 0,
        "tvdb_show": 1,
    }
    assert payload["schema_version"] == "3.0.3"


def test_system_mappings_before_ingest_is_degraded(client: TestClient) -> None:
    response = client.get("/api/system/mappings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing"
    assert payload["stale"] is True
    assert payload["row_count"] == 0


def test_episode_map_forward_and_reverse(
    client: TestClient, mapping_payload: dict[str, Any]
) -> None:
    _ingest(client, mapping_payload)

    forward = client.get(
        "/api/metadata/anime/154587/episode-map", params={"season": 1, "episode": 5}
    )
    reverse = client.get("/api/metadata/anime/154587/episode-map", params={"episode": 30})

    assert forward.status_code == 200
    payload = forward.json()
    assert payload["source"]["provider"] == "imdb_show"
    assert payload["source"]["scope"] == "s1"
    assert payload["source"]["episode"] == 5
    assert payload["target"]["provider"] == "anilist"
    assert payload["target"]["id"] == "154587"
    assert payload["target"]["episode"] == 5
    assert payload["stale"] is False

    assert reverse.status_code == 200
    reverse_payload = reverse.json()
    assert reverse_payload["source"]["provider"] == "anilist"
    assert reverse_payload["source"]["episode"] == 30
    assert reverse_payload["target"]["provider"] == "imdb_show"
    assert reverse_payload["target"]["scope"] == "s2"
    assert reverse_payload["target"]["episode"] == 2


def test_episode_map_returns_404_without_mapping(
    client: TestClient, mapping_payload: dict[str, Any]
) -> None:
    _ingest(client, mapping_payload)

    response = client.get(
        "/api/metadata/anime/154587/episode-map", params={"season": 9, "episode": 1}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "MAPPING_NOT_FOUND"


def test_episode_fix_writes_override(client: TestClient, mapping_payload: dict[str, Any]) -> None:
    _ingest(client, mapping_payload)
    with client.app.state.session_factory() as session:
        title = titles.create_title(
            session,
            kind="anime",
            title="Frieren",
            imdb_id="tt1234567",
            anilist_id=154587,
        )
        title_id = title.id

    response = client.post(
        f"/api/titles/{title_id}/episode-fix",
        json={"season": 1, "episode": 5, "target_episode": 7},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title_id"] == title_id
    assert payload["override"]["target_provider"] == "anilist"
    assert payload["override"]["target_id"] == "154587"
    assert payload["override"]["target_episode"] == 7

    with client.app.state.session_factory() as session:
        title = titles.get_title(session, title_id)
        assert title is not None
        resolved = mapping.find_override(session, title.id, 1, 5)
        assert resolved is not None
        assert resolved.target_episode == 7


def test_episode_fix_requires_existing_title(client: TestClient) -> None:
    response = client.post(
        "/api/titles/does-not-exist/episode-fix",
        json={"season": 1, "episode": 1, "target_episode": 1},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TITLE_NOT_FOUND"


def test_episode_fix_requires_a_target_id(
    client: TestClient, mapping_payload: dict[str, Any]
) -> None:
    _ingest(client, mapping_payload)
    with client.app.state.session_factory() as session:
        title = titles.create_title(session, kind="anime", title="No ids")
        title_id = title.id

    response = client.post(
        f"/api/titles/{title_id}/episode-fix",
        json={"season": 1, "episode": 1, "target_episode": 1},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "MAPPING_NO_TARGET"


def test_episode_map_validates_query(client: TestClient) -> None:
    assert client.get("/api/metadata/anime/0/episode-map?episode=1").status_code == 422
    assert client.get("/api/metadata/anime/1/episode-map").status_code == 422
    assert client.get("/api/metadata/anime/1/episode-map?episode=0").status_code == 422


def test_refresh_endpoint_downloads_and_ingests(
    client: TestClient,
    mapping_payload: dict[str, Any],
    compress: Any,
) -> None:
    url = mapping.DATASET_URL
    data = compress(mapping_payload)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, content=data))
        response = client.post("/api/system/mappings/refresh")

    assert route.call_count == 1
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] > 0
    assert payload["status"]["status"] == "ok"
    assert mapping.dataset_path(client.app.state.settings).is_file()


def test_refresh_endpoint_keeps_dataset_on_bad_download(
    client: TestClient, mapping_payload: dict[str, Any]
) -> None:
    report = _ingest(client, mapping_payload)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(mapping.DATASET_URL).mock(return_value=httpx.Response(500))
        response = client.post("/api/system/mappings/refresh")

    assert response.status_code == 502
    assert response.json()["code"] == "MAPPING_UNAVAILABLE"
    assert client.get("/api/system/mappings").json()["last_update"] == report.last_update
