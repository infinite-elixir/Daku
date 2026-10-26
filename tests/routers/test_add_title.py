from __future__ import annotations

from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from daku.models import AnimeMapping, Title
from daku.services.metadata import anilist, cinemeta


def _mapping(imdb_id: str = "tt22248376", anilist_id: str = "154587") -> AnimeMapping:
    return AnimeMapping(
        src_provider="imdb_movie",
        src_id=imdb_id,
        src_scope="",
        src_start=1,
        src_end=1,
        tgt_provider="anilist",
        tgt_id=anilist_id,
        tgt_scope="",
        tgt_start=1,
        tgt_end=1,
        ratio=1,
    )


def test_mapped_anime_add_stores_both_identities(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with client.app.state.session_factory() as session:
        session.add(_mapping())
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt22248376",
                "kind": "series",
                "anime": True,
                "anilist_id": 154587,
                "metadata_source": "anilist",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["kind"] == "anime"
    assert payload["imdb_id"] == "tt22248376"
    assert payload["anilist_id"] == 154587
    assert payload["metadata_source"] == "anilist"
    assert payload["episode_count"] == 28
    assert payload["episodes"][0]["absolute"] == 1

    with client.app.state.session_factory() as session:
        row = session.scalar(select(Title).where(Title.imdb_id == "tt22248376"))
        assert row is not None
        assert row.kind == "anime"


def test_mapped_anime_add_fails_cleanly_when_anilist_is_down(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    with client.app.state.session_factory() as session:
        session.add(_mapping())
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(500))
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt22248376",
                "kind": "series",
                "anime": True,
                "anilist_id": 154587,
            },
        )

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Title).where(Title.imdb_id == "tt22248376")) is None


def test_existing_series_is_never_mutated_by_an_anime_add(
    app_client: tuple[TestClient, object], load_fixture: Callable[[str], object]
) -> None:
    client, _ = app_client
    with respx.mock(assert_all_called=False) as mock:
        mock.get(cinemeta.meta_url("series", "tt22248376")).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        created = client.post(
            "/api/titles",
            json={"source": "imdb", "source_id": "tt22248376", "kind": "series"},
        )
    assert created.status_code == 201
    series_id = created.json()["id"]

    with client.app.state.session_factory() as session:
        session.add(_mapping())
        session.commit()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(200, json=load_fixture("anilist_detail.json"))
        )
        response = client.post(
            "/api/titles",
            json={
                "source": "imdb",
                "source_id": "tt22248376",
                "kind": "series",
                "anime": True,
                "anilist_id": 154587,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == series_id
    assert payload["kind"] == "series"
    assert payload["classification"]["is_anime"] is True
    assert payload["classification"]["suggested_anilist_id"] == 154587
