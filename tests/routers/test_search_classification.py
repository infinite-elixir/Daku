from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from daku.models import AnimeMapping
from daku.services.metadata import anilist, imdb


def _seed_mapping(client: TestClient, src_id: str, tgt_id: str) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            AnimeMapping(
                src_provider="imdb_movie",
                src_id=src_id,
                src_scope="",
                src_start=1,
                src_end=1,
                tgt_provider="anilist",
                tgt_id=tgt_id,
                tgt_scope="",
                tgt_start=1,
                tgt_end=1,
                ratio=1,
            )
        )
        session.commit()


def _suggestions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"d": rows}


def _anilist_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"Page": {"media": rows}}}


def _media(
    anilist_id: int,
    *,
    title: str,
    year: int,
    fmt: str = "TV",
    country: str = "JP",
) -> dict[str, Any]:
    return {
        "id": anilist_id,
        "format": fmt,
        "episodes": 12,
        "seasonYear": year,
        "countryOfOrigin": country,
        "title": {"romaji": title, "english": title, "native": None},
        "synonyms": [],
        "coverImage": None,
        "description": None,
    }


def test_search_uses_one_mapping_lookup(app_client: tuple[TestClient, object]) -> None:
    client, _ = app_client
    _seed_mapping(client, "tt0944947", "999")
    query = "game of thrones"
    payload = _suggestions(
        [
            {"id": "tt0944947", "l": "Game of Thrones", "y": 2011, "qid": "tvSeries"},
            {"id": "tt1111111", "l": "Mini Series", "y": 2019, "qid": "TV mini-series"},
        ]
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=payload)
        )
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(200, json=_anilist_page([])))
        response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    results = {row["source_id"]: row for row in response.json()["results"]}
    assert results["tt0944947"]["anime"] is True
    assert results["tt0944947"]["anilist_id"] == 999
    assert results["tt0944947"]["classification_reason"] == "Detected via AniBridge mapping"
    assert "anime" not in results["tt1111111"]


def test_search_falls_back_to_anilist_exact_match(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    # A non-empty dataset is required before Daku will guess from AniList.
    _seed_mapping(client, "tt9999999", "1")
    query = "frieren"
    payload = _suggestions(
        [
            {
                "id": "tt22248376",
                "l": "Frieren: Beyond Journey's End",
                "y": 2023,
                "qid": "tvSeries",
            }
        ]
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=payload)
        )
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_anilist_page(
                    [
                        _media(
                            154587,
                            title="Frieren: Beyond Journey's End",
                            year=2023,
                        )
                    ]
                ),
            )
        )
        response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["anime"] is True
    assert row["anilist_id"] == 154587
    assert row["classification_reason"] == "AniList exact match"


def test_search_does_not_classify_western_animation(
    app_client: tuple[TestClient, object],
) -> None:
    client, _ = app_client
    _seed_mapping(client, "tt9999999", "1")
    query = "arcane"
    payload = _suggestions([{"id": "tt11126994", "l": "Arcane", "y": 2021, "qid": "tvSeries"}])
    with respx.mock(assert_all_called=False) as mock:
        mock.get(imdb.build_suggestion_url(query)).mock(
            return_value=httpx.Response(200, json=payload)
        )
        mock.post(anilist.ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_anilist_page(
                    [
                        _media(
                            999,
                            title="Arcane",
                            year=2021,
                            country="US",
                        )
                    ]
                ),
            )
        )
        response = client.get("/api/search", params={"q": query})

    assert response.status_code == 200
    assert "anime" not in response.json()["results"][0]
