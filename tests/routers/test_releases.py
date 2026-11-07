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
from daku.services.torrents.torrentscsv import build_search_url


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # Router tests use the plain URL; prefix construction has its own unit tests.
    plain = settings.model_copy(update={"torrentio_config": "", "providers": []})
    with TestClient(create_app(plain, configure_logs=False)) as test_client:
        # Keep the default suite off the keyword source; its own tests opt in.
        from daku.repositories import settings as settings_repo

        with test_client.app.state.session_factory() as session:  # type: ignore[attr-defined]
            for kind in ("movie", "series"):
                settings_repo.set_setting(session, f"sources_{kind}", ["torrentio"])
        yield test_client


@pytest.fixture(autouse=True)
def _reset_keyword_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the per-process keyword throttle from sleeping between tests."""

    from daku.services.torrents import throttle as throttle_module

    monkeypatch.setattr(throttle_module, "_last_request", {})


def _add_title(
    client: TestClient,
    *,
    kind: str,
    imdb_id: str | None,
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


def _set_sources(client: TestClient, kind: str, values: list[str]) -> None:
    from daku.repositories import settings as settings_repo

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        settings_repo.set_setting(session, f"sources_{kind}", values)


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
    # Seven valid streams; the default filter drops cam and unknown.
    assert payload["total"] == 5
    assert payload["total_unfiltered"] == 7
    assert payload["returned"] == 5
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 3, "720p": 1}
    assert payload["min_quality"] is None
    assert payload["providers"] == []

    releases = payload["releases"]
    assert [r["seeders"] for r in releases] == [120, 43, 12, 7, 5]
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
    assert payload["total"] == 7
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
        "rarbg",
        "1337x",
        "ext",
        "thepiratebay",
        "kickasstorrents",
        "torrentgalaxy",
        "magnetdl",
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
    assert payload["sources"] == ["torrentio", "torrents-csv"]
    assert payload["selected_sources"] == ["torrentio"]
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
    assert payload["total"] == 4
    assert payload["returned"] == 4
    assert [row["quality"] for row in payload["releases"]] == [
        "2160p",
        "1080p",
        "1080p",
        "1080p",
    ]
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 3}
    assert payload["total_unfiltered"] == 7


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
    assert payload["total"] == 5
    assert payload["quality_counts"] == {"2160p": 1, "1080p": 3, "720p": 1}


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


# ---------------------------------------------------------------- Torrents-CSV source


def _csv_payload(
    load_fixture: Callable[[str], object],
) -> list[dict[str, Any]]:
    payload = load_fixture("torrents_csv_search.json")
    assert isinstance(payload, list)
    return payload


def test_movie_merges_torrentio_and_torrents_csv(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    _set_sources(client, "movie", ["torrentio", "torrents-csv"])
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    torrentio_url = build_stream_url("", "movie", "tt1254207")
    csv_url = build_search_url("Big Buck Bunny 2008")

    with respx.mock(assert_all_called=True) as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        mock.get(csv_url).mock(return_value=httpx.Response(200, json=_csv_payload(load_fixture)))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources"] == ["torrentio", "torrents-csv"]
    assert body["sources_degraded"] == []
    # Five Torrentio rows after the default filter plus the one matching CSV row.
    assert body["total"] == 6
    csv_rows = [row for row in body["releases"] if row["provider"] == "torrents-csv"]
    assert len(csv_rows) == 1
    assert csv_rows[0]["metadata_json"]["resolved_from"] == "torrents-csv"
    assert csv_rows[0]["quality"] == "2160p"
    assert csv_rows[0]["size_bytes"] == 5_000_000_000


def test_duplicate_hashes_across_sources_collapse_to_highest_seeders(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    _set_sources(client, "movie", ["torrentio", "torrents-csv"])
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    torrentio_url = build_stream_url("", "movie", "tt1254207")
    csv_url = build_search_url("Big Buck Bunny 2008")
    payload = [
        {
            "infohash": "1111111111111111111111111111111111111111",
            "name": "Big.Buck.Bunny.2008.1080p.WEB-DL.mkv",
            "size_bytes": 2_000_000_000,
            "seeders": 500,
        }
    ]

    with respx.mock(assert_all_called=True) as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        mock.get(csv_url).mock(return_value=httpx.Response(200, json=payload))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicates_removed"] == 1
    matching = [
        row
        for row in body["releases"]
        if row["info_hash"] == "1111111111111111111111111111111111111111"
    ]
    assert len(matching) == 1
    assert matching[0]["seeders"] == 500
    assert matching[0]["provider"] == "torrents-csv"


def test_degraded_torrents_csv_keeps_torrentio_rows(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    _set_sources(client, "movie", ["torrentio", "torrents-csv"])
    title_id = _add_title(client, kind="movie", imdb_id="tt1254207")
    torrentio_url = build_stream_url("", "movie", "tt1254207")
    csv_url = build_search_url("Big Buck Bunny 2008")

    with respx.mock(assert_all_called=True) as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=_fixture(load_fixture)))
        mock.get(csv_url).mock(return_value=httpx.Response(500))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources_degraded"] == ["torrents-csv"]
    assert body["total"] == 5
    assert all(row["provider"] != "torrents-csv" for row in body["releases"])


def test_title_without_imdb_still_uses_keyword_source(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    _set_sources(client, "movie", ["torrents-csv"])
    title_id = _add_title(client, kind="movie", imdb_id=None)
    csv_url = build_search_url("Big Buck Bunny 2008")

    with respx.mock(assert_all_called=True) as mock:
        mock.get(csv_url).mock(return_value=httpx.Response(200, json=_csv_payload(load_fixture)))
        response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources"] == ["torrents-csv"]
    assert body["type"] == ""
    assert body["id"] == ""
    assert [row["provider"] for row in body["releases"]] == ["torrents-csv"]


def test_title_without_imdb_and_only_imdb_source_returns_422(client: TestClient) -> None:
    _set_sources(client, "movie", ["torrentio"])
    title_id = _add_title(client, kind="movie", imdb_id=None)

    response = client.get(f"/api/titles/{title_id}/releases")

    assert response.status_code == 422
    assert response.json()["code"] == "RELEASE_SOURCE_UNAVAILABLE"


def test_torrents_csv_is_not_available_for_anime(client: TestClient) -> None:
    options = client.get("/api/releases/options", params={"kind": "anime"})

    assert options.status_code == 200
    assert options.json()["sources"] == ["torrentio", "animetosho"]
    assert "torrents-csv" not in options.json()["sources"]

    title_id = _add_title(client, kind="anime", imdb_id=None, title="Big Buck Bunny")
    response = client.get(
        f"/api/titles/{title_id}/releases",
        params={"season": 1, "episode": 1, "sources": "torrents-csv"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SOURCES"
