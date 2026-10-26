from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from daku.models import Episode, MediaFile, Release, Title
from daku.services.metadata import anilist, cinemeta
from daku.utils import new_id, utc_now_iso

SERIES_IMDB = "tt0944947"


def _series(client: TestClient, load_fixture: Callable[[str], object]) -> dict[str, Any]:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", SERIES_IMDB)).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        response = client.post(
            "/api/titles",
            json={"source": "imdb", "source_id": SERIES_IMDB, "kind": "series"},
        )
    assert response.status_code == 201
    return response.json()


def _session(client: TestClient) -> Any:
    return client.app.state.session_factory()


def test_movie_to_series_keeps_saved_releases(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    movie_meta = {
        "meta": {
            "id": "tt0111161",
            "type": "movie",
            "name": "The Shawshank Redemption",
            "releaseInfo": "1994",
        }
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("movie", "tt0111161")).mock(
            return_value=httpx.Response(200, json=movie_meta)
        )
        created = client.post(
            "/api/titles",
            json={"source": "imdb", "source_id": "tt0111161", "kind": "movie"},
        )
    assert created.status_code == 201
    title_id = created.json()["id"]

    with _session(client) as session:
        session.add(
            Release(
                id=new_id(),
                title_id=title_id,
                info_hash="abc123",
                is_saved=True,
                saved_at=utc_now_iso(),
            )
        )
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt0111161")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        response = client.patch(
            f"/api/titles/{title_id}/identity",
            json={"kind": "series", "title": "The Shawshank Redemption"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "series"
    assert payload["episode_count"] == 3
    assert payload["overrides"]["fields"]["kind"] == "series"

    with _session(client) as session:
        releases = session.scalars(select(Release).where(Release.title_id == title_id)).all()
        assert len(releases) == 1
        assert releases[0].is_saved is True


def test_series_to_anime_renumbers_in_place(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    created = _series(client, load_fixture)
    title_id = created["id"]
    original_ids = [episode["id"] for episode in created["episodes"]]

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        response = client.patch(
            f"/api/titles/{title_id}/identity",
            json={"kind": "anime", "anilist_id": 154587},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "anime"
    assert payload["anilist_id"] == 154587
    assert payload["metadata_source"] == "anilist"
    assert payload["episode_count"] == 28
    first_three = [episode["id"] for episode in payload["episodes"][:3]]
    assert first_three == original_ids
    assert [episode["absolute"] for episode in payload["episodes"][:3]] == [1, 2, 3]


def test_kind_to_movie_is_blocked_then_force_keeps_media_rows(
    app_client: tuple[TestClient, object],
    load_fixture: Callable[[str], object],
    tmp_path: Path,
) -> None:
    client, _ = app_client
    created = _series(client, load_fixture)
    title_id = created["id"]
    episode_id = created["episodes"][0]["id"]

    media_path = tmp_path / "episode.mkv"
    media_path.write_bytes(b"video")
    with _session(client) as session:
        session.add(
            MediaFile(
                id=new_id(),
                title_id=title_id,
                episode_id=episode_id,
                src_path=str(media_path),
                lib_path=str(media_path),
                is_primary=True,
                created_at=utc_now_iso(),
            )
        )
        session.commit()

    blocked = client.patch(f"/api/titles/{title_id}/identity", json={"kind": "movie"})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "KIND_HAS_EPISODES"
    assert blocked.json()["detail"]["episodes"] == 3

    forced = client.patch(
        f"/api/titles/{title_id}/identity",
        json={"kind": "movie", "force": True},
    )
    assert forced.status_code == 200
    assert forced.json()["kind"] == "movie"
    assert media_path.exists()
    with _session(client) as session:
        assert session.scalar(select(func.count(MediaFile.id))) == 1
        assert session.scalar(select(func.count(Episode.id))) == 0


def test_duplicate_imdb_id_returns_409(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    first = _series(client, load_fixture)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt1111111")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        second = client.post(
            "/api/titles",
            json={"source": "imdb", "source_id": "tt1111111", "kind": "series"},
        )
    assert second.status_code == 201

    response = client.patch(
        f"/api/titles/{second.json()['id']}/identity",
        json={"imdb_id": first["imdb_id"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "IDENTITY_CONFLICT"
    assert response.json()["detail"]["title_id"] == first["id"]


def test_season_offset_moves_episodes_without_duplicates(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="series",
            title="Split Cour",
            imdb_id="tt7777777",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.add_all(
            [
                Episode(id=new_id(), title_id=title.id, season=2, episode=1, status="missing"),
                Episode(id=new_id(), title_id=title.id, season=2, episode=2, status="missing"),
            ]
        )
        session.commit()
        title_id = title.id

    response = client.put(
        f"/api/titles/{title_id}/episode-map",
        json={"season_offsets": [{"provider_season": 2, "library_season": 1}], "episodes": []},
    )
    assert response.status_code == 200
    rows = [(episode["season"], episode["episode"]) for episode in response.json()["episodes"]]
    assert rows == [(1, 1), (1, 2)]
    assert response.json()["overrides"]["season_offsets"] == [
        {"provider_season": 2, "library_season": 1}
    ]


def test_episode_override_is_visible_and_reverts(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="series",
            title="Renumber",
            imdb_id="tt8888888",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.add_all(
            [
                Episode(id=new_id(), title_id=title.id, season=1, episode=1, status="missing"),
                Episode(id=new_id(), title_id=title.id, season=1, episode=2, status="missing"),
            ]
        )
        session.commit()
        title_id = title.id

    applied = client.put(
        f"/api/titles/{title_id}/episode-map",
        json={
            "season_offsets": [],
            "episodes": [
                {
                    "season": 1,
                    "episode": 2,
                    "target_season": 1,
                    "target_episode": 9,
                    "absolute": 18,
                }
            ],
        },
    )
    assert applied.status_code == 200
    rows = {(ep["season"], ep["episode"]): ep for ep in applied.json()["episodes"]}
    assert (1, 9) in rows
    assert rows[(1, 9)]["absolute"] == 18
    assert (1, 2) not in rows

    reverted = client.put(
        f"/api/titles/{title_id}/episode-map",
        json={"season_offsets": [], "episodes": []},
    )
    assert reverted.status_code == 200
    rows = {(ep["season"], ep["episode"]) for ep in reverted.json()["episodes"]}
    assert rows == {(1, 1), (1, 2)}
    assert reverted.json()["overrides"]["episodes"] == []


def test_refresh_cannot_overwrite_overridden_field(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    created = _series(client, load_fixture)
    title_id = created["id"]

    patched = client.patch(f"/api/titles/{title_id}/identity", json={"title": "My Custom Title"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "My Custom Title"

    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", SERIES_IMDB)).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        refreshed = client.post(f"/api/titles/{title_id}/metadata/refresh", json={"source": "auto"})

    assert refreshed.status_code == 200
    assert refreshed.json()["overridden_fields"] == ["title"]
    detail = client.get(f"/api/titles/{title_id}").json()
    assert detail["title"] == "My Custom Title"

    removed = client.delete(f"/api/titles/{title_id}/overrides/title")
    assert removed.status_code == 200
    assert removed.json()["overrides"]["fields"] == {}
