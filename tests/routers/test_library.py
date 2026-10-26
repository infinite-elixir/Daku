from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daku.app import create_app
from daku.config import Settings
from daku.models import Download, Episode, MediaFile, Release, Title
from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_files_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata import anilist, cinemeta
from daku.services.metadata.models import EpisodeRef

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


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def _session(client: TestClient) -> Session:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory()


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _mock_movie(mock: respx.MockRouter) -> None:
    mock.get(cinemeta.meta_url("movie", "tt1234567")).mock(
        return_value=httpx.Response(200, json=MOVIE_META)
    )


def _add_movie(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/titles",
        json={"source": "imdb", "source_id": "tt1234567", "kind": "movie"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_adding_movie_persists_across_restart(
    settings: Settings,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        with TestClient(create_app(settings, configure_logs=False)) as first:
            added = _add_movie(first)

    with TestClient(create_app(settings, configure_logs=False)) as second:
        response = second.get("/api/titles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == added["id"]
    assert payload["items"][0]["title"] == "Big Buck Bunny"
    assert payload["items"][0]["availability"] == "none"


def test_adding_creates_no_download_rows(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        added = _add_movie(client)

    with _session(client) as session:
        assert _count(session, Download) == 0
        assert _count(session, Release) == 0
        assert _count(session, MediaFile) == 0
        assert session.get(Title, added["id"]) is not None


def test_adding_without_metadata_still_stores_entry(
    client: TestClient,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt7654321")).mock(return_value=httpx.Response(500))
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt7654321",
                "kind": "movie",
                "title": "Offline Movie",
                "year": 1999,
            },
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["title"] == "Offline Movie"
    assert payload["availability"] == "none"
    with _session(client) as session:
        assert _count(session, Download) == 0


def test_re_adding_is_idempotent(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        first = _add_movie(client)

    # No metadata route is mocked: an existing title must be returned from the DB.
    second = client.post(
        "/api/titles",
        json={"source": "imdb", "source_id": "tt1234567", "kind": "movie"},
    )

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    with _session(client) as session:
        assert _count(session, Title) == 1


def test_patch_wanted_is_filterable_and_starts_nothing(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        added = _add_movie(client)

    response = client.patch(f"/api/titles/{added['id']}", json={"wanted": True})
    assert response.status_code == 200
    assert response.json()["wanted"] is True
    assert response.json()["wanted_at"] is not None

    filtered = client.get("/api/titles", params={"wanted": "true"})
    assert [item["id"] for item in filtered.json()["items"]] == [added["id"]]
    excluded = client.get("/api/titles", params={"wanted": "false"})
    assert excluded.json()["items"] == []

    with _session(client) as session:
        assert _count(session, Download) == 0


def test_patch_folder_name_and_kind(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        added = _add_movie(client)

    response = client.patch(
        f"/api/titles/{added['id']}",
        json={"folder_name": "Big Buck Bunny (2008)", "kind": "series"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["folder_name"] == "Big Buck Bunny (2008)"
    assert payload["kind"] == "series"


def test_empty_patch_is_rejected(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_movie(mock)
        added = _add_movie(client)

    response = client.patch(f"/api/titles/{added['id']}", json={})
    assert response.status_code == 422


def test_adding_series_persists_every_episode(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt0944947")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        response = client.post(
            "/api/titles",
            json={"source": "cinemeta", "source_id": "tt0944947", "kind": "series"},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["episode_count"] == 3
    assert [episode["label"] for episode in payload["episodes"]] == [
        "S01E01",
        "S01E02",
        "S02E01",
    ]
    assert all(episode["absolute"] is None for episode in payload["episodes"])
    assert all(episode["status"] == "missing" for episode in payload["episodes"])

    with _session(client) as session:
        rows = episodes_repo.list_episodes(session, payload["id"])
        assert [(row.season, row.episode) for row in rows] == [(1, 1), (1, 2), (2, 1)]


def test_adding_anime_persists_episodes_without_duplicates(
    client: TestClient, load_fixture: Callable[[str], object]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        first = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": "154587", "kind": "anime"},
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": "154587", "kind": "anime"},
        )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    payload = first.json()
    assert payload["anilist_id"] == 154587
    assert payload["mal_id"] == 52991
    assert payload["episode_count"] == 28
    assert payload["episodes"][0]["status"] == "missing"
    assert payload["episodes"][0]["absolute"] == 1

    with _session(client) as session:
        assert _count(session, Title) == 1
        assert _count(session, Episode) == 28


def test_list_pagination_filters_and_sort(client: TestClient) -> None:
    with _session(client) as session:
        alpha = titles_repo.create_title(session, kind="movie", title="Alpha", year=2001)
        beta = titles_repo.create_title(session, kind="series", title="Beta", year=2002)
        gamma = titles_repo.create_title(session, kind="anime", title="Gamma", year=2003)
        titles_repo.set_wanted(session, beta.id, True)
        episodes_repo.sync_episodes(
            session,
            title_id=beta.id,
            episodes=[
                # Two episodes, one file below: the title is partial.
                EpisodeRef(season=1, episode=1),
                EpisodeRef(season=1, episode=2),
            ],
        )
        ep1 = episodes_repo.find_episode(session, title_id=beta.id, season=1, episode=1)
        media_files_repo.create_media_file(
            session,
            title_id=beta.id,
            episode_id=ep1.id if ep1 else None,
            src_path="/tmp/beta-e1.mkv",
        )

    first_page = client.get("/api/titles", params={"limit": 2, "offset": 0, "sort": "title"})
    second_page = client.get("/api/titles", params={"limit": 2, "offset": 2, "sort": "title"})
    assert first_page.json()["total"] == 3
    assert [item["title"] for item in first_page.json()["items"]] == ["Alpha", "Beta"]
    assert [item["title"] for item in second_page.json()["items"]] == ["Gamma"]

    movies = client.get("/api/titles", params={"kind": "movie"})
    assert [item["id"] for item in movies.json()["items"]] == [alpha.id]

    partial = client.get("/api/titles", params={"availability": "partial"})
    assert [item["id"] for item in partial.json()["items"]] == [beta.id]

    no_file = client.get("/api/titles", params={"availability": "none"})
    assert {item["id"] for item in no_file.json()["items"]} == {alpha.id, gamma.id}

    wanted = client.get("/api/titles", params={"wanted": "true"})
    assert [item["id"] for item in wanted.json()["items"]] == [beta.id]

    search = client.get("/api/titles", params={"q": "amm"})
    assert [item["id"] for item in search.json()["items"]] == [gamma.id]


def test_detail_includes_episodes_and_saved_release_count(client: TestClient) -> None:
    with _session(client) as session:
        title = titles_repo.create_title(session, kind="series", title="Show")
        episodes_repo.sync_episodes(
            session,
            title_id=title.id,
            episodes=[EpisodeRef(season=1, episode=1)],
        )
        releases_repo.create_release(session, title_id=title.id, info_hash="h1", is_saved=True)
        releases_repo.create_release(session, title_id=title.id, info_hash="h2", is_saved=False)

    response = client.get(f"/api/titles/{title.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["saved_release_count"] == 1
    assert payload["episode_count"] == 1
    assert payload["availability"] == "none"


def test_delete_keeps_files_by_default(client: TestClient, tmp_path: Path) -> None:
    library_file = tmp_path / "movie.mkv"
    library_file.write_bytes(b"video")
    with _session(client) as session:
        title = titles_repo.create_title(session, kind="movie", title="Movie")
        media_files_repo.create_media_file(
            session, title_id=title.id, src_path=str(library_file), is_primary=True
        )

    response = client.delete(f"/api/titles/{title.id}")
    assert response.status_code == 200
    assert response.json()["files_deleted"] == 0
    assert library_file.exists()
    with _session(client) as session:
        assert _count(session, Title) == 0
        assert _count(session, MediaFile) == 0


def test_delete_with_keep_files_false_removes_recorded_files(
    client: TestClient, tmp_path: Path
) -> None:
    library_file = tmp_path / "movie.mkv"
    library_file.write_bytes(b"video")
    with _session(client) as session:
        title = titles_repo.create_title(session, kind="movie", title="Movie")
        media_files_repo.create_media_file(
            session, title_id=title.id, src_path=str(library_file), is_primary=True
        )

    response = client.delete(f"/api/titles/{title.id}", params={"keep_files": "false"})
    assert response.status_code == 200
    assert response.json()["files_deleted"] == 1
    assert not library_file.exists()


def test_missing_title_returns_404(client: TestClient) -> None:
    assert client.get("/api/titles/nope").status_code == 404
    assert client.patch("/api/titles/nope", json={"wanted": True}).status_code == 404
    assert client.delete("/api/titles/nope").status_code == 404


def test_invalid_source_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/titles",
        json={"source": "anilist", "source_id": "154587", "kind": "movie"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TITLE_SOURCE"
