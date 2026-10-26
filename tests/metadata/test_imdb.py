from __future__ import annotations

from collections.abc import Callable

import pytest

from daku.services.metadata import imdb


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Big Buck Bunny", "big_buck_bunny"),
        ("  Big   Buck Bunny  ", "big_buck_bunny"),
        ("Spider-Man: No Way Home", "spider-man_no_way_home"),
        ("Schindler's List", "schindler_s_list"),
        ("Amélie!", "am_lie_"),
        ("Dune.Part.Two", "dune.part.two"),
    ],
)
def test_normalise_query(query: str, expected: str) -> None:
    assert imdb.normalise_query(query) == expected


def test_suggestion_url_uses_first_character_segment() -> None:
    assert (
        imdb.build_suggestion_url("Big Buck Bunny")
        == "https://v2.sg.media-imdb.com/suggestion/b/big_buck_bunny.json"
    )
    assert imdb.build_suggestion_url("Amélie!").endswith("/a/am_lie_.json")


def test_parse_suggestions_handles_missing_fields_and_types(
    load_fixture: Callable[[str], object],
) -> None:
    payload = load_fixture("imdb_suggestions.json")

    candidates = imdb.parse_suggestions(payload)
    by_id = {candidate.source_id: candidate for candidate in candidates}

    assert set(by_id) == {
        "tt1234567",
        "tt0944947",
        "tt1111111",
        "tt2222222",
        "tt4444444",
    }

    bbb = by_id["tt1234567"]
    assert bbb.kind == "movie"
    assert bbb.year == 2008
    assert bbb.title == "Big Buck Bunny"
    assert bbb.poster_url == "https://images.example/bbb.jpg"
    assert bbb.raw_type == "movie"

    got = by_id["tt0944947"]
    assert got.kind == "series"
    assert got.year == 2011
    assert got.raw_type == "tvSeries"

    mini = by_id["tt1111111"]
    assert mini.kind == "series"
    assert mini.year == 2019
    assert mini.poster_url is None

    odd = by_id["tt2222222"]
    assert odd.kind == "movie"
    assert odd.raw_type == "videoGame"

    short = by_id["tt4444444"]
    assert short.kind == "movie"
    assert short.year is None


def test_parse_suggestions_rejects_non_payloads() -> None:
    assert imdb.parse_suggestions(None) == []
    assert imdb.parse_suggestions([]) == []
    assert imdb.parse_suggestions({"d": "nope"}) == []
    assert imdb.parse_suggestions({"d": [{"id": "not-an-id", "l": "Bad id"}]}) == []
