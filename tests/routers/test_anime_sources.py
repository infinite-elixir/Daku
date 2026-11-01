from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import titles as titles_repo
from daku.services.metadata.aliases import store_generated_aliases
from daku.services.torrents.torrentio import build_stream_url


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    plain = settings.model_copy(update={"torrentio_config": "", "providers": []})
    with TestClient(create_app(plain, configure_logs=False)) as test_client:
        yield test_client


def _add_anime(client: TestClient, *, imdb_id: str | None = "tt1234567") -> str:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = titles_repo.create_title(
            session,
            kind="anime",
            title="Zom 100",
            year=2023,
            imdb_id=imdb_id,
            anilist_id=12345,
        )
        return row.id


def _feed(load_fixture: Callable[[str], object]) -> dict[str, Any] | list[Any]:
    payload = load_fixture("animetosho_feed.json")
    assert isinstance(payload, list)
    return payload


def test_anime_search_merges_torrentio_and_animetosho(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_anime(client)
    torrentio_url = build_stream_url("", "series", "tt1234567:1:1")
    torrentio_payload = {
        "streams": [
            {
                "title": "Torrentio\n1080p\nZom 100\n👤 90 💾 1 GB ⚙️ yts",
                "infoHash": "a" * 40,
                "behaviorHints": {"filename": "Zom.100.S01E01.1080p.mkv"},
            }
        ]
    }

    with respx.mock() as mock:
        torrentio_route = mock.get(torrentio_url).mock(
            return_value=httpx.Response(200, json=torrentio_payload)
        )
        feed_route = mock.get(host="feed.animetosho.net").mock(
            return_value=httpx.Response(
                200,
                text=json.dumps(_feed(load_fixture)),
                headers={"content-type": "application/json"},
            )
        )
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 1},
        )

    assert response.status_code == 200, response.text
    assert torrentio_route.called
    assert feed_route.called
    payload = response.json()
    assert payload["sources"] == ["torrentio", "animetosho"]
    assert payload["sources_degraded"] == []
    assert payload["duplicates_removed"] >= 1
    assert payload["total"] == 2
    assert {row["resolved_from"] for row in payload["releases"]} == {
        "torrentio",
        "animetosho",
    }
    duplicate = next(row for row in payload["releases"] if row["info_hash"] == "a" * 40)
    assert duplicate["seeders"] == 90
    assert duplicate["resolved_from"] == "torrentio"
    anime_row = next(row for row in payload["releases"] if row["provider"] == "animetosho")
    assert anime_row["resolved_from"] == "animetosho"


def test_movie_search_never_calls_animetosho(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        title_id = titles_repo.create_title(
            session,
            kind="movie",
            title="Big Buck Bunny",
            year=2008,
            imdb_id="tt1254207",
        ).id
    torrentio_url = build_stream_url("", "movie", "tt1254207")
    torrentio_payload = load_fixture("torrentio_big_buck_bunny.json")

    with respx.mock() as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=torrentio_payload))
        response = client.get(f"/api/titles/{title_id}/releases")
        assert not any("animetosho" in str(call.request.url) for call in mock.calls)

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == ["torrentio"]


def test_animetosho_failure_is_degraded_not_502(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    title_id = _add_anime(client)
    torrentio_url = build_stream_url("", "series", "tt1234567:1:1")
    torrentio_payload = {
        "streams": [
            {
                "title": "Torrentio\n1080p\nZom 100\n👤 12 💾 1 GB ⚙️ yts",
                "infoHash": "d" * 40,
                "behaviorHints": {"filename": "Zom.100.S01E01.1080p.mkv"},
            }
        ]
    }

    with respx.mock() as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=torrentio_payload))
        mock.get(host="feed.animetosho.net").mock(return_value=httpx.Response(500))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 1},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sources_degraded"] == ["animetosho"]
    assert [row["info_hash"] for row in payload["releases"]] == ["d" * 40]


def test_torrentio_only_source_skips_animetosho(client: TestClient) -> None:
    title_id = _add_anime(client)
    torrentio_url = build_stream_url("", "series", "tt1234567:1:1")
    torrentio_payload = {
        "streams": [
            {
                "title": "Torrentio\n1080p\nZom 100\n👤 12 💾 1 GB ⚙️ yts",
                "infoHash": "e" * 40,
                "behaviorHints": {"filename": "Zom.100.S01E01.1080p.mkv"},
            }
        ]
    }

    with respx.mock() as mock:
        mock.get(torrentio_url).mock(return_value=httpx.Response(200, json=torrentio_payload))
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 1, "sources": "torrentio"},
        )
        assert not any("animetosho" in str(call.request.url) for call in mock.calls)

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == ["torrentio"]


def test_invalid_source_selection_is_422(client: TestClient) -> None:
    title_id = _add_anime(client)

    junk = client.get(
        f"/api/titles/{title_id}/releases",
        params={"season": 1, "episode": 1, "sources": "junk"},
    )
    assert junk.status_code == 422
    assert junk.json()["code"] == "INVALID_SOURCES"


def test_every_alias_is_searched_within_the_query_budget(client: TestClient) -> None:
    title_id = _add_anime(client)
    aliases = [f"Alias {index}" for index in range(1, 7)]
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = titles_repo.get_title(session, title_id)
        assert row is not None
        store_generated_aliases(session, row, aliases)
        session.commit()

    with respx.mock() as mock:
        route = mock.get(host="feed.animetosho.net").mock(
            return_value=httpx.Response(200, text="[]")
        )
        response = client.get(
            f"/api/titles/{title_id}/releases",
            params={"season": 1, "episode": 1, "sources": "animetosho"},
        )

    assert response.status_code == 200, response.text
    queries = [call.request.url.params["q"] for call in route.calls]
    expected = ["Zom 100", *aliases]
    assert len(queries) <= 8
    assert queries[: len(expected)] == expected
    body = response.json()
    assert body["search"]["aliases"] == len(expected)
    assert body["search"]["queries"] == len(queries)
