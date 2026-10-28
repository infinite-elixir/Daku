from __future__ import annotations

from collections.abc import Callable

from daku.services.metadata import cinemeta


def test_catalog_url_encodes_query() -> None:
    assert (
        cinemeta.catalog_url("movie", "big buck bunny")
        == "https://v3-cinemeta.strem.io/catalog/movie/top/search=big%20buck%20bunny.json"
    )
    assert (
        cinemeta.meta_url("series", "tt0944947")
        == "https://v3-cinemeta.strem.io/meta/series/tt0944947.json"
    )


def test_parse_catalog(load_fixture: Callable[[str], object]) -> None:
    payload = load_fixture("cinemeta_catalog_movie.json")

    candidates = cinemeta.parse_catalog(payload, "movie")
    by_id = {candidate.source_id: candidate for candidate in candidates}

    assert set(by_id) == {"tt1234567", "tt15239678"}
    assert by_id["tt15239678"].title == "Dune: Part Two"
    assert by_id["tt15239678"].year == 2024
    assert by_id["tt15239678"].source == "cinemeta"
    assert by_id["tt1234567"].poster_url == "https://images.example/bbb-cinemeta.jpg"


def test_parse_meta_orders_episodes(load_fixture: Callable[[str], object]) -> None:
    payload = load_fixture("cinemeta_meta_series.json")

    detail = cinemeta.parse_meta(payload, "series", "tt0944947")

    assert detail is not None
    assert detail.title == "Game of Thrones"
    assert detail.year == 2011
    assert detail.kind == "series"
    assert detail.banner_url == "https://images.example/got-banner.jpg"
    assert [episode.label for episode in detail.episodes] == [
        "S01E01",
        "S01E02",
        "S02E01",
    ]
    assert detail.episodes[0].title == "Winter Is Coming"
    assert detail.episodes[0].imdb_id == "tt0944947:1:1"
    assert detail.episodes[0].air_date == "2011-04-17"
    assert detail.episodes[2].title == "The North Remembers"


def test_parse_meta_handles_missing_fields() -> None:
    assert cinemeta.parse_meta(None, "movie", "tt1") is None
    assert cinemeta.parse_meta({}, "movie", "tt1") is None
    assert cinemeta.parse_meta({"meta": {}}, "movie", "tt1") is not None
