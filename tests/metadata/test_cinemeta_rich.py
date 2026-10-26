from __future__ import annotations

from typing import Any

import pytest

from daku.services.metadata import cinemeta

RICH_META: dict[str, Any] = {
    "meta": {
        "id": "tt15239678",
        "type": "movie",
        "name": "Dune: Part Two",
        "releaseInfo": "2024",
        "description": "Paul Atreides unites with the Fremen.",
        "imdbRating": "8.5",
        "runtime": "167 min",
        "genres": ["Action", "Adventure", "Drama"],
        "cast": [
            "Timothée Chalamet",
            "Zendaya",
            "Rebecca Ferguson",
            "Javier Bardem",
            "Josh Brolin",
            "Austin Butler",
            "Florence Pugh",
            "Dave Bautista",
            "Christopher Walken",
            "Léa Seydoux",
            "Charlotte Rampling",
        ],
        "director": ["Denis Villeneuve"],
        "writer": ["Denis Villeneuve", "Jon Spaihts"],
    }
}


def test_parse_meta_reads_ratings_and_credits() -> None:
    detail = cinemeta.parse_meta(RICH_META, "movie", "tt15239678")

    assert detail is not None
    assert detail.imdb_rating == 8.5
    assert detail.runtime_minutes == 167
    assert detail.genres == ["Action", "Adventure", "Drama"]
    assert detail.cast[0] == "Timothée Chalamet"
    assert len(detail.cast) == 10  # stored cap
    assert detail.directors == ["Denis Villeneuve"]
    assert detail.writers == ["Denis Villeneuve", "Jon Spaihts"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("167 min", 167),
        ("167 minutes", 167),
        ("90", 90),
        (90, 90),
        ("N/A", None),
        ("", None),
        (None, None),
        (0, None),
    ],
)
def test_runtime_parsing(raw: Any, expected: int | None) -> None:
    detail = cinemeta.parse_meta({"meta": {"name": "X", "runtime": raw}}, "movie", "tt1")

    assert detail is not None
    assert detail.runtime_minutes == expected


def test_genre_singular_and_comma_strings() -> None:
    detail = cinemeta.parse_meta(
        {
            "meta": {
                "name": "X",
                "genre": "Drama, Mystery",
                "genres": ["Drama"],
                "cast": "A, B , C",
                "imdbRating": "N/A",
            }
        },
        "movie",
        "tt1",
    )

    assert detail is not None
    assert detail.genres == ["Drama", "Mystery"]
    assert detail.cast == ["A", "B", "C"]
    assert detail.imdb_rating is None


def test_missing_rich_fields_stay_empty() -> None:
    detail = cinemeta.parse_meta({"meta": {"name": "X"}}, "movie", "tt1")

    assert detail is not None
    assert detail.imdb_rating is None
    assert detail.runtime_minutes is None
    assert detail.genres == []
    assert detail.cast == []
    assert detail.directors == []
    assert detail.writers == []


def test_to_dict_keeps_display_fields_for_non_anime() -> None:
    detail = cinemeta.parse_meta(RICH_META, "movie", "tt15239678")

    assert detail is not None
    payload = detail.to_dict()
    assert payload["imdb_rating"] == 8.5
    assert payload["runtime_minutes"] == 167
    assert payload["genres"] == ["Action", "Adventure", "Drama"]
    assert payload["cast"][0] == "Timothée Chalamet"
    assert payload["directors"] == ["Denis Villeneuve"]
