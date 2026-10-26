from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import titles as titles_repo
from daku.services.torrents.torrentio import build_stream_url


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # Router tests use the plain URL; prefix construction has its own unit tests.
    plain = settings.model_copy(update={"torrentio_config": "", "providers": []})
    with TestClient(create_app(plain, configure_logs=False)) as test_client:
        yield test_client


def _add_title(
    client: TestClient,
    *,
    kind: str,
    imdb_id: str,
    title: str = "Big Buck Bunny",
) -> str:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = titles_repo.create_title(
            session,
            kind=kind,
            title=title,
            year=2008,
            imdb_id=imdb_id,
        )
        return row.id


def _fixture(load_fixture: Callable[[str], object]) -> dict[str, Any]:
    payload = load_fixture("torrentio_big_buck_bunny.json")
    assert isinstance(payload, dict)
    return payload


def test_movie_releases(client: TestClient, load_fixture: Callable[[str], object]) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200, response.text
    assert route.called
    payload = response.json()
    assert payload["title_id"] == title_id
    assert payload["type"] == "movie"
    assert payload["id"] == "tt1254207"
    assert payload["stale"] is False
    assert payload["cached_at"]
    assert payload["sort"] == "seeders"
    assert payload["quality_filter"] == ["cam", "scr", "unknown"]
    # Six valid streams; the default filter drops cam and unknown.
    assert payload["total"] == 4
    assert payload["total_unfiltered"] == 6
    assert payload["returned"] == 4
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 2, "720p": 1}
    assert payload["min_quality"] is None
    assert payload["providers"] == []

    releases = payload["releases"]
    assert [r["seeders"] for r in releases] == [120, 43, 12, 7]
    first = releases[0]
    # T-007 keys, plus the T-008 row identity/saved annotations.
    assert {
        "info_hash",
        "filename",
        "quality",
        "size_bytes",
        "seeders",
        "provider",
        "magnet",
        "metadata_json",
        "release_id",
        "is_saved",
        "dead",
    } <= set(first)
    assert first["release_id"] is None
    assert first["is_saved"] is False
    assert first["metadata_json"]["resolved_from"] == "torrentio"
    assert first["quality"] == "2160p"
    assert first["metadata_json"]["source"] == "WEB-DL"


def test_episode_releases(client: TestClient, load_fixture: Callable[[str], object]) -> None:
    title_id = _add_title(client, kind="series", imdb_id="tt0944947", title="Game of Thrones")
    url = build_stream_url("", "series", "tt0944947:2:5")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 2, "episode": 5},
        )

    assert response.status_code == 200, response.text
    assert route.called
    assert response.json()["type"] == "series"
    assert response.json()["id"] == "tt0944947:2:5"


def test_series_requires_episode_or_explicit_pack(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_title(client, kind="series", imdb_id="tt0944947", title="Game of Thrones")

    response = client.get(f"/api/titles/{title_id}/releases")
    assert response.status_code == 422
    assert response.json()["code"] == "PACK_REQUIRED"

    season_url = build_stream_url("", "series", "tt0944947:2")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(season_url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        packed = client.get(
            f"/api/titles/{title_id}/releases",
            params={"pack": "true", "season": 2},
        )

    assert packed.status_code == 200
    assert packed.json()["id"] == "tt0944947:2"


def test_cam_can_be_reenabled(client: TestClient, load_fixture: Callable[[str], object]) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"quality_filter": "", "sort": "quality", "limit": 2},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["quality_filter"] == []
    assert payload["total"] == 6
    assert len(payload["releases"]) == 2
    assert [r["quality"] for r in payload["releases"]] == ["2160p", "1080p"]


def test_empty_result_is_200_not_404(client: TestClient) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json={"streams": []}))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200
    assert response.json()["releases"] == []
    assert response.json()["total"] == 0


def test_stale_fallback_keeps_last_payload(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(
            side_effect=[
                httpx.Response(200, json=_fixture(load_fixture)),
                httpx.Response(500),
            ]
        )
        first = client.get(f"/api/titles/{title_id}/releases")
        assert first.status_code == 200
        assert first.json()["stale"] is False

        # Expire the entry without waiting an hour.
        cache = client.app.state.releases_cache  # type: ignore[attr-defined]
        cache.ttl_seconds = -1

        second = client.get(f"/api/titles/{title_id}/releases")

    assert second.status_code == 200
    assert second.json()["stale"] is True
    assert second.json()["cached_at"] == first.json()["cached_at"]
    assert len(second.json()["releases"]) == len(first.json()["releases"])


def test_upstream_failure_without_cache_is_502(client: TestClient) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(503))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 502
    assert response.json()["code"] == "RELEASES_UNAVAILABLE"


def test_unknown_title_is_404(client: TestClient) -> None:
    response = client.get("/api/titles/does-not-exist/releases")
    assert response.status_code == 404
    assert response.json()["code"] == "TITLE_NOT_FOUND"


def test_movie_rejects_season_parameter(client: TestClient) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    response = client.get(f"/api/titles/{title_id}/releases", params={"season": 1})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_RELEASE_QUERY"


def test_options_endpoint_lists_controls(client: TestClient) -> None:
    response = client.get("/api/releases/options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"] == [
        "yts",
        "eztv",
        "1337x",
        "thepiratebay",
        "torrentgalaxy",
    ]
    assert payload["quality_labels"] == [
        "2160p",
        "1440p",
        "1080p",
        "720p",
        "576p",
        "480p",
    ]
    assert payload["sorts"] == ["seeders", "quality", "size"]
    assert payload["defaults"] == {
        "providers": [],
        "min_quality": None,
        "sort": "seeders",
        "limit": 20,
    }
    assert payload["limit_steps"] == [20, 50, 100, 200]


def test_min_quality_filters_rows_and_counts(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"min_quality": "1080p", "sort": "quality"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["min_quality"] == "1080p"
    assert payload["total"] == 3
    assert payload["returned"] == 3
    assert [row["quality"] for row in payload["releases"]] == ["2160p", "1080p", "1080p"]
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 2}
    assert payload["total_unfiltered"] == 6


def test_empty_min_quality_means_any(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"min_quality": ""},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["min_quality"] is None
    assert payload["total"] == 4
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 2, "720p": 1}


def test_invalid_providers_return_stable_422(client: TestClient) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")

    response = client.get(
        f"/api/titles/{title_id}/releases",
        params={"providers": "yts,not a provider!"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PROVIDERS"


def test_provider_selection_changes_url_and_is_echoed(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("providers=yts,eztv", "movie", "tt1254207")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"providers": " YTS, eztv,yts "},
        )

    assert response.status_code == 200, response.text
    assert route.called
    assert response.json()["providers"] == ["yts", "eztv"]


def test_duplicate_hashes_collapse_to_highest_seeders(client: TestClient) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")
    payload = {
        "streams": [
            {
                "title": "Torrentio\n1080p\nExample\n👤 10 💾 1 GB ⚙️ yts",
                "infoHash": "aaaa",
                "behaviorHints": {"filename": "Example.1080p.mkv"},
            },
            {
                "title": "Torrentio\n1080p\nExample\n👤 90 💾 1 GB ⚙️ eztv",
                "infoHash": "AAAA",
                "behaviorHints": {"filename": "Example.1080p.mkv"},
            },
        ]
    }

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=payload))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200
    body = response.json()
    assert body["duplicates_removed"] == 1
    assert body["total"] == 1
    assert body["releases"][0]["seeders"] == 90
    assert body["releases"][0]["provider"] == "eztv"


def test_limit_ceiling_is_200(client: TestClient, load_fixture: Callable[[str], object]) -> None:
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    url = build_stream_url("", "movie", "tt1254207")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"limit": 200},
        )
        rejected = client.get(
            f"/api/titles/{title_id}/releases",
            params={"limit": 201},
        )

    assert response.status_code == 200
    assert response.json()["limit"] == 200
    assert rejected.status_code == 422
