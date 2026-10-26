from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from daku.models import AnimeMapping, Episode, MediaFile, Release, Title, TitleSeason
from daku.services.metadata import anilist, cinemeta, jikan
from daku.services.metadata.anime import resolve_title_episode
from daku.utils import new_id, utc_now_iso

EDGERUNNERS_IMDB = "tt12590266"
EDGERUNNERS_ANILIST = 120377
EDGERUNNERS_MAL = 42310


@pytest.fixture(autouse=True)
def _clear_jikan_cache() -> None:
    jikan.clear_cache()


def _session(client: TestClient) -> Any:
    return client.app.state.session_factory()


def _jikan_page(titles: list[str], *, last: int = 1) -> dict[str, Any]:
    return {
        "pagination": {"last_visible_page": last},
        "data": [
            {
                "mal_id": index + 1,
                "title": title,
                "aired": "2022-09-13",
                "filler": False,
                "recap": False,
            }
            for index, title in enumerate(titles)
        ],
    }


def _mock_jikan(mock: respx.Router, mal_id: int, titles: list[str]) -> None:
    mock.get(f"https://api.jikan.moe/v4/anime/{mal_id}/episodes?page=1").mock(
        return_value=httpx.Response(200, json=_jikan_page(titles))
    )


def _mock_edgerunners(mock: respx.Router, load_fixture: Callable[[str], object]) -> None:
    mock.post(anilist.ENDPOINT).mock(
        return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
    )
    mock.get(cinemeta.meta_url("series", EDGERUNNERS_IMDB)).mock(
        return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_edgerunners.json"))
    )
    _mock_jikan(mock, EDGERUNNERS_MAL, [])


def _mapping(
    *,
    src_provider: str = "imdb_show",
    src_id: str,
    src_scope: str,
    tgt_id: str,
    src_start: int = 1,
    src_end: int = 0,
    tgt_start: int = 1,
    tgt_end: int = 0,
) -> AnimeMapping:
    return AnimeMapping(
        src_provider=src_provider,
        src_id=src_id,
        src_scope=src_scope,
        src_start=src_start,
        src_end=src_end,
        tgt_provider="anilist",
        tgt_id=tgt_id,
        tgt_scope="",
        tgt_start=tgt_start,
        tgt_end=tgt_end,
        ratio=1,
    )


def _add_edgerunners(client: TestClient, load_fixture: Callable[[str], object]) -> dict[str, Any]:
    with respx.mock(assert_all_called=False) as mock:
        _mock_edgerunners(mock, load_fixture)
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": EDGERUNNERS_IMDB,
                "kind": "series",
                "anime": True,
                "anilist_id": EDGERUNNERS_ANILIST,
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


def test_edgerunners_add_uses_imdb_structure_and_keeps_anilist_identity(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    payload = _add_edgerunners(client, load_fixture)

    assert payload["kind"] == "anime"
    assert payload["imdb_id"] == EDGERUNNERS_IMDB
    assert payload["anilist_id"] == EDGERUNNERS_ANILIST
    assert [
        (season["library_season"], season["episode_count"]) for season in payload["seasons"]
    ] == [
        (0, 1),
        (1, 10),
    ]
    assert payload["episode_count"] == 11
    assert [episode["label"] for episode in payload["episodes"]][:2] == ["S00E01", "S01E01"]
    assert payload["episodes"][0]["name"] == "Let You Down"
    assert payload["episodes"][-1]["label"] == "S01E10"
    assert payload["episodes"][-1]["absolute"] == 10

    with _session(client) as session:
        title = session.scalar(select(Title).where(Title.imdb_id == EDGERUNNERS_IMDB))
        assert title is not None
        assert title.anilist_id == EDGERUNNERS_ANILIST
        assert title.kind == "anime"
        rows = session.scalars(
            select(TitleSeason)
            .where(TitleSeason.title_id == title.id)
            .order_by(TitleSeason.library_season)
        ).all()
        assert [(row.library_season, row.source, row.provider_season) for row in rows] == [
            (0, "cinemeta", 0),
            (1, "cinemeta", 1),
        ]


def test_edgerunners_preview_matches_the_added_structure(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with _session(client) as session:
        session.add(
            _mapping(
                src_id=EDGERUNNERS_IMDB,
                src_scope="s1",
                tgt_id=str(EDGERUNNERS_ANILIST),
            )
        )
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", EDGERUNNERS_IMDB)).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_edgerunners.json"))
        )
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
        )
        response = client.get(
            "/api/metadata/preview",
            params={"source": "imdb", "source_id": EDGERUNNERS_IMDB, "kind": "series"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kind"] == "series"
    assert payload["anilist_id"] == EDGERUNNERS_ANILIST
    assert [
        (season["library_season"], season["episode_count"]) for season in payload["seasons"]
    ] == [
        (0, 1),
        (1, 10),
    ]
    assert payload["episode_count"] == 11
    assert payload["episodes"][0]["label"] == "S00E01"


def test_multi_cour_add_uses_all_aggregate_seasons_without_duplicates(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with _session(client) as session:
        session.add_all(
            [
                _mapping(src_id="tt7000002", src_scope="s1", tgt_id="300"),
                _mapping(src_id="tt7000002", src_scope="s2", tgt_id="301"),
            ]
        )
        session.commit()

    cinemeta_payload = {
        "meta": {
            "id": "tt7000002",
            "type": "series",
            "name": "Two Cours",
            "releaseInfo": "2024",
            "videos": [
                {
                    "id": "tt7000002:1:1",
                    "season": 1,
                    "episode": 1,
                    "name": "Cour One A",
                    "released": "2024-01-01",
                },
                {
                    "id": "tt7000002:1:2",
                    "season": 1,
                    "episode": 2,
                    "name": "Cour One B",
                    "released": "2024-01-08",
                },
                {
                    "id": "tt7000002:2:1",
                    "season": 2,
                    "episode": 1,
                    "name": "Cour Two A",
                    "released": "2024-04-01",
                },
                {
                    "id": "tt7000002:2:2",
                    "season": 2,
                    "episode": 2,
                    "name": "Cour Two B",
                    "released": "2024-04-08",
                },
            ],
        }
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
        )
        mock.get(cinemeta.meta_url("series", "tt7000002")).mock(
            return_value=httpx.Response(200, json=cinemeta_payload)
        )
        _mock_jikan(mock, EDGERUNNERS_MAL, [])
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt7000002",
                "kind": "series",
                "anime": True,
                "anilist_id": 300,
            },
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert [
        (season["library_season"], season["episode_count"]) for season in payload["seasons"]
    ] == [
        (1, 2),
        (2, 2),
    ]
    assert [
        (episode["season"], episode["episode"], episode["absolute"])
        for episode in payload["episodes"]
    ] == [
        (1, 1, 1),
        (1, 2, 2),
        (2, 1, 3),
        (2, 2, 4),
    ]
    assert [season["anilist_id"] for season in payload["seasons"]] == [300, 301]
    assert payload["episode_count"] == 4


def test_anime_add_fills_jikan_episode_names(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    titles = [f"Frieren episode {number}" for number in range(1, 29)]
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        _mock_jikan(mock, 52991, titles)
        response = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": "154587", "kind": "anime"},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["episodes"][0]["name"] == "Frieren episode 1"
    assert payload["episodes"][27]["name"] == "Frieren episode 28"


def test_anime_preview_shows_the_same_jikan_names(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        _mock_jikan(mock, 52991, ["First Journey", "Second Journey"])
        response = client.get(
            "/api/metadata/preview",
            params={"source": "anilist", "source_id": "154587", "kind": "anime"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["episodes"][0]["name"] == "First Journey"
    assert payload["episodes"][1]["name"] == "Second Journey"


def test_anime_add_still_succeeds_when_jikan_fails(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        mock.get("https://api.jikan.moe/v4/anime/52991/episodes?page=1").mock(
            return_value=httpx.Response(500)
        )
        response = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": "154587", "kind": "anime"},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["episode_count"] == 28
    assert payload["episodes"][0]["name"] is None


def test_anime_without_idmal_uses_streaming_episode_titles(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    payload = {
        "data": {
            "Media": {
                "id": 555,
                "idMal": None,
                "format": "TV",
                "episodes": 3,
                "seasonYear": 2021,
                "title": {"romaji": "No MAL", "english": "No MAL", "native": None},
                "coverImage": {"extraLarge": None},
                "description": None,
                "genres": [],
                "studios": {"nodes": []},
                "streamingEpisodes": [
                    {"title": "Episode 1 - The Journey", "site": "Crunchyroll"},
                    {"title": "Episode 2 - The Storm", "site": "Crunchyroll"},
                    {"title": "Episode 3 - Home", "site": "Crunchyroll"},
                ],
                "relations": {"edges": []},
            }
        }
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
        response = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": "555", "kind": "anime"},
        )

    assert response.status_code == 201, response.text
    titles = [episode["name"] for episode in response.json()["episodes"]]
    assert titles == ["The Journey", "The Storm", "Home"]


def test_anilist_only_add_is_one_season_and_put_adds_related_seasons(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
        )
        _mock_jikan(mock, EDGERUNNERS_MAL, [])
        created = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": str(EDGERUNNERS_ANILIST), "kind": "anime"},
        )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["imdb_id"] is None
    assert [
        (season["library_season"], season["episode_count"]) for season in payload["seasons"]
    ] == [(1, 10)]
    title_id = payload["id"]

    second = {
        "data": {
            "Media": {
                "id": 99999,
                "idMal": None,
                "format": "TV",
                "episodes": 2,
                "seasonYear": 2023,
                "title": {"romaji": "Related Cour", "english": "Related Cour", "native": None},
                "coverImage": {"extraLarge": None},
                "description": None,
                "genres": [],
                "studios": {"nodes": []},
                "streamingEpisodes": [],
                "relations": {"edges": []},
            }
        }
    }

    def anilist_response(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["variables"]["id"] == 99999:
            return httpx.Response(200, json=second)
        return httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(side_effect=anilist_response)
        response = client.put(
            f"/api/titles/{title_id}/seasons",
            json={
                "seasons": [
                    {"library_season": 1, "anilist_id": EDGERUNNERS_ANILIST},
                    {"library_season": 2, "anilist_id": 99999},
                ]
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [
        (season["library_season"], season["episode_count"], season["anilist_id"])
        for season in payload["seasons"]
    ] == [
        (1, 10, EDGERUNNERS_ANILIST),
        (2, 2, 99999),
    ]
    rows = {(episode["season"], episode["episode"]): episode for episode in payload["episodes"]}
    assert rows[(2, 1)]["absolute"] == 11
    assert rows[(2, 1)]["name"] is None


def test_put_seasons_rejects_duplicates_and_gaps(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="AniList only",
            anilist_id=100,
            added_at=utc_now_iso(),
            metadata_source="anilist",
        )
        session.add(title)
        session.commit()
        title_id = title.id

    duplicate = client.put(
        f"/api/titles/{title_id}/seasons",
        json={
            "seasons": [
                {"library_season": 1, "anilist_id": 100},
                {"library_season": 2, "anilist_id": 100},
            ]
        },
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "DUPLICATE_SEASON"

    gap = client.put(
        f"/api/titles/{title_id}/seasons",
        json={
            "seasons": [
                {"library_season": 1, "anilist_id": 100},
                {"library_season": 3, "anilist_id": 200},
            ]
        },
    )
    assert gap.status_code == 422
    assert gap.json()["code"] == "NON_CONTIGUOUS_SEASONS"


def test_refreshing_a_rebuilt_structure_is_idempotent_and_keeps_files_and_releases(
    app_client: tuple[TestClient, object],
    load_fixture: Callable[[str], object],
    tmp_path: Path,
) -> None:
    client, _ = app_client
    created = _add_edgerunners(client, load_fixture)
    title_id = created["id"]
    patched = client.patch(f"/api/titles/{title_id}/identity", json={"title": "My Edgerunners"})
    assert patched.status_code == 200
    episode_ids = [episode["id"] for episode in created["episodes"]]
    season_one = [episode for episode in created["episodes"] if episode["season"] == 1]
    media_path = tmp_path / "edgerunners-s01e01.mkv"
    media_path.write_bytes(b"video")
    with _session(client) as session:
        session.add(
            Release(
                id=new_id(),
                title_id=title_id,
                episode_id=season_one[1]["id"],
                info_hash="edgerunners-release",
                is_saved=True,
                saved_at=utc_now_iso(),
            )
        )
        session.add(
            MediaFile(
                id=new_id(),
                title_id=title_id,
                episode_id=season_one[0]["id"],
                src_path=str(media_path),
                lib_path=str(media_path),
                is_primary=True,
                created_at=utc_now_iso(),
            )
        )
        session.commit()

    for _ in range(2):
        with respx.mock(assert_all_called=False) as mock:
            _mock_edgerunners(mock, load_fixture)
            response = client.post(
                f"/api/titles/{title_id}/metadata/refresh", json={"source": "auto"}
            )
        assert response.status_code == 200, response.text

    detail = client.get(f"/api/titles/{title_id}").json()
    assert detail["title"] == "My Edgerunners"
    assert detail["episode_count"] == 11
    assert [episode["id"] for episode in detail["episodes"]] == episode_ids
    assert detail["episodes"][0]["label"] == "S00E01"
    assert [
        (season["library_season"], season["episode_count"]) for season in detail["seasons"]
    ] == [
        (0, 1),
        (1, 10),
    ]
    assert detail["episodes"][-1]["absolute"] == 10
    assert media_path.exists()

    with _session(client) as session:
        assert (
            session.scalar(select(func.count(Release.id)).where(Release.title_id == title_id)) == 1
        )
        assert (
            session.scalar(select(func.count(MediaFile.id)).where(MediaFile.title_id == title_id))
            == 1
        )


def test_refresh_keeps_structure_when_the_structure_provider_is_down(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="Cyberpunk: Edgerunners",
            imdb_id=EDGERUNNERS_IMDB,
            anilist_id=EDGERUNNERS_ANILIST,
            mal_id=EDGERUNNERS_MAL,
            added_at=utc_now_iso(),
            metadata_source="anilist",
        )
        session.add(title)
        session.add(
            TitleSeason(
                title_id=title.id,
                library_season=1,
                source="cinemeta",
                source_id=EDGERUNNERS_IMDB,
                provider_season=1,
                anilist_id=EDGERUNNERS_ANILIST,
                absolute_offset=0,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
        )
        session.add(
            Episode(
                id=new_id(),
                title_id=title.id,
                season=1,
                episode=1,
                absolute=1,
                name="Keep me",
                status="missing",
            )
        )
        session.commit()
        title_id = title.id

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
        )
        mock.get(cinemeta.meta_url("series", EDGERUNNERS_IMDB)).mock(
            return_value=httpx.Response(503)
        )
        _mock_jikan(mock, EDGERUNNERS_MAL, [])
        response = client.post(f"/api/titles/{title_id}/metadata/refresh", json={"source": "auto"})

    assert response.status_code == 200, response.text
    detail = client.get(f"/api/titles/{title_id}").json()
    assert detail["episode_count"] == 1
    assert detail["episodes"][0]["name"] == "Keep me"
    assert [season["library_season"] for season in detail["seasons"]] == [1]


def test_rebuild_keeps_episode_ids_releases_and_files(
    app_client: tuple[TestClient, object],
    load_fixture: Callable[[str], object],
    tmp_path: Path,
) -> None:
    client, _ = app_client
    # Add AniList-only, then give the title an IMDb identity like the fixer would.
    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_edgerunners.json"))
        )
        _mock_jikan(mock, EDGERUNNERS_MAL, [])
        created = client.post(
            "/api/titles",
            json={"source": "anilist", "source_id": str(EDGERUNNERS_ANILIST), "kind": "anime"},
        )
    assert created.status_code == 201, created.text
    title_id = created.json()["id"]
    patched = client.patch(f"/api/titles/{title_id}/identity", json={"imdb_id": EDGERUNNERS_IMDB})
    assert patched.status_code == 200, patched.text

    detail = client.get(f"/api/titles/{title_id}").json()
    original_ids = [episode["id"] for episode in detail["episodes"]]
    first_episode = detail["episodes"][0]
    media_path = tmp_path / "edgerunners.mkv"
    media_path.write_bytes(b"video")
    with _session(client) as session:
        session.add(
            Release(
                id=new_id(),
                title_id=title_id,
                episode_id=first_episode["id"],
                info_hash="rebuild-release",
                is_saved=True,
                saved_at=utc_now_iso(),
            )
        )
        session.add(
            MediaFile(
                id=new_id(),
                title_id=title_id,
                episode_id=first_episode["id"],
                src_path=str(media_path),
                lib_path=str(media_path),
                is_primary=True,
                created_at=utc_now_iso(),
            )
        )
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        _mock_edgerunners(mock, load_fixture)
        response = client.post(
            f"/api/titles/{title_id}/seasons/rebuild",
            json={"source": "imdb"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [
        (season["library_season"], season["episode_count"]) for season in payload["seasons"]
    ] == [
        (0, 1),
        (1, 10),
    ]
    rows = {(episode["season"], episode["episode"]): episode for episode in payload["episodes"]}
    assert rows[(1, 1)]["id"] == original_ids[0]
    assert [rows[(1, number)]["id"] for number in range(1, 11)] == original_ids
    assert rows[(0, 1)]["id"] not in original_ids
    assert payload["rebuild"]["kept_episodes"] == 10
    assert payload["rebuild"]["created"] == 1
    assert payload["rebuild"]["files_at_risk"] == 0
    assert media_path.exists()

    with _session(client) as session:
        release = session.scalar(select(Release).where(Release.title_id == title_id))
        assert release is not None
        assert release.episode_id == original_ids[0]
        media = session.scalar(select(MediaFile).where(MediaFile.title_id == title_id))
        assert media is not None
        assert media.episode_id == original_ids[0]


def test_rebuild_requires_confirmation_before_dropping_episodes_with_files(
    app_client: tuple[TestClient, object],
    load_fixture: Callable[[str], object],
    tmp_path: Path,
) -> None:
    client, _ = app_client
    media_path = tmp_path / "episode-12.mkv"
    media_path.write_bytes(b"video")
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="Long AniList entry",
            imdb_id=EDGERUNNERS_IMDB,
            anilist_id=EDGERUNNERS_ANILIST,
            added_at=utc_now_iso(),
            metadata_source="anilist",
        )
        session.add(title)
        episodes = [
            Episode(
                id=new_id(),
                title_id=title.id,
                season=1,
                episode=number,
                absolute=number,
                status="missing",
            )
            for number in range(1, 13)
        ]
        session.add_all(episodes)
        session.flush()
        session.add(
            MediaFile(
                id=new_id(),
                title_id=title.id,
                episode_id=episodes[-1].id,
                src_path=str(media_path),
                lib_path=str(media_path),
                is_primary=True,
                created_at=utc_now_iso(),
            )
        )
        session.commit()
        title_id = title.id
        media_episode_id = episodes[-1].id

    with respx.mock(assert_all_called=False) as mock:
        _mock_edgerunners(mock, load_fixture)
        blocked = client.post(
            f"/api/titles/{title_id}/seasons/rebuild",
            json={"source": "imdb"},
        )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "REBUILD_REQUIRES_CONFIRM"
    assert blocked.json()["detail"] == {"episodes": 2, "files": 1}

    with respx.mock(assert_all_called=False) as mock:
        _mock_edgerunners(mock, load_fixture)
        confirmed = client.post(
            f"/api/titles/{title_id}/seasons/rebuild",
            json={"source": "imdb", "confirm": True},
        )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["rebuild"]["files_at_risk"] == 1
    assert media_path.exists()
    with _session(client) as session:
        media = session.scalar(select(MediaFile).where(MediaFile.title_id == title_id))
        assert media is not None
        assert media.episode_id == media_episode_id
        assert session.get(Episode, media_episode_id) is not None


def test_split_cour_season_uses_its_own_anilist_identity(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    with _session(client) as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="Split cour",
            imdb_id="tt7000001",
            anilist_id=100,
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.add_all(
            [
                TitleSeason(
                    title_id=title.id,
                    library_season=1,
                    source="cinemeta",
                    source_id="tt7000001",
                    provider_season=1,
                    anilist_id=100,
                    absolute_offset=0,
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                ),
                TitleSeason(
                    title_id=title.id,
                    library_season=2,
                    source="cinemeta",
                    source_id="tt7000001",
                    provider_season=2,
                    anilist_id=200,
                    absolute_offset=12,
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                ),
            ]
        )
        session.add_all(
            [
                _mapping(src_id="tt7000001", src_scope="s1", tgt_id="100"),
                _mapping(src_id="tt7000001", src_scope="s2", tgt_id="200"),
            ]
        )
        session.commit()
        session.refresh(title)

        first = resolve_title_episode(session, title, 1, 1)
        second = resolve_title_episode(session, title, 2, 1)

    assert first is not None
    assert first.target_id == "100"
    assert second is not None
    assert second.target_id == "200"
