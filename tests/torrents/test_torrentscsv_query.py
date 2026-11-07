from __future__ import annotations

import pytest

from daku.services.torrents.base import KeywordQuery, Release
from daku.services.torrents.magnets import build_magnet
from daku.services.torrents.torrentscsv import (
    SEARCH_BASE,
    build_query,
    build_search_url,
    gate_releases,
)


def _release(filename: str, seeders: int = 10) -> Release:
    info_hash = f"{seeders:040x}"
    return Release(
        info_hash=info_hash,
        filename=filename,
        quality="1080p",
        size_bytes=1_000_000_000,
        seeders=seeders,
        provider="torrents-csv",
        magnet=build_magnet(info_hash, filename),
        metadata={},
    )


def test_search_url_shape_and_encoding() -> None:
    assert build_search_url("Severance S02E05") == (
        "https://torrents-csv.com/service/search?size=100&q=Severance+S02E05"
    )
    assert build_search_url("Zom 100 & Zombie") == (
        "https://torrents-csv.com/service/search?size=100&q=Zom+100+%26+Zombie"
    )


def test_search_url_strips_controls_and_caps_length() -> None:
    assert build_search_url("bad\x00query\n") == (
        "https://torrents-csv.com/service/search?size=100&q=bad+query"
    )
    capped = build_search_url("a" * 200)
    assert capped.startswith(SEARCH_BASE)
    assert capped.split("q=", 1)[1] == "a" * 120


def test_movie_query_uses_year_when_known() -> None:
    assert build_query(KeywordQuery(title="Big Buck Bunny", year=2008)) == ("Big Buck Bunny 2008")
    assert build_query(KeywordQuery(title="Big Buck Bunny")) == "Big Buck Bunny"


def test_episode_query_uses_season_episode_notation() -> None:
    assert (
        build_query(KeywordQuery(title="Severance", season=2, episode=5, year=2022))
        == "Severance S02E05"
    )


def test_pack_query_uses_season_only_when_packed() -> None:
    assert build_query(KeywordQuery(title="Severance", season=2, pack=True)) == (
        "Severance Season 2"
    )
    # A pack without a season stays a bare title even when a year is known.
    assert build_query(KeywordQuery(title="Severance", year=2022, pack=True)) == "Severance"


def test_year_gate_keeps_close_years_and_yearless_names() -> None:
    query = KeywordQuery(title="Big Buck Bunny", year=2008)
    releases = [
        _release("Big.Buck.Bunny.2008.1080p.mkv"),
        _release("Big.Buck.Bunny.2009.1080p.mkv"),
        _release("Big.Buck.Bunny.1080p.mkv"),
        _release("Big.Buck.Bunny.1990.1080p.mkv"),
    ]

    kept = gate_releases(releases, query)

    assert [release.filename for release in kept] == [
        "Big.Buck.Bunny.2008.1080p.mkv",
        "Big.Buck.Bunny.2009.1080p.mkv",
        "Big.Buck.Bunny.1080p.mkv",
    ]


def test_title_gate_drops_unrelated_rows() -> None:
    query = KeywordQuery(title="Big Buck Bunny", year=2008)
    releases = [
        _release("Big.Buck.Bunny.2008.1080p.mkv"),
        _release("Other.Movie.2008.1080p.mkv"),
        _release("Big.Buck.Bunny.2008.1080p.mkv"),
    ]

    kept = gate_releases(releases, query)

    # The duplicate is kept here; the shared pipeline dedupes by info hash.
    assert len(kept) == 2


def test_episode_gate_keeps_exact_match_only() -> None:
    query = KeywordQuery(title="Big Buck Bunny", season=2, episode=5)
    releases = [
        _release("Big.Buck.Bunny.S02E05.1080p.mkv", seeders=3),
        _release("Big.Buck.Bunny.S02E06.1080p.mkv", seeders=2),
        _release("Big.Buck.Bunny.S03E05.1080p.mkv", seeders=1),
        _release("Other.Show.S02E05.1080p.mkv", seeders=0),
    ]

    kept = gate_releases(releases, query)

    assert [release.filename for release in kept] == ["Big.Buck.Bunny.S02E05.1080p.mkv"]
    assert kept[0].is_pack is False


def test_covering_pack_is_flagged_for_an_episode_request() -> None:
    query = KeywordQuery(title="Big Buck Bunny", season=2, episode=5)
    releases = [
        _release("Big.Buck.Bunny.S02.1080p.BluRay.mkv", seeders=9),
        _release("Big.Buck.Bunny.S03.1080p.BluRay.mkv", seeders=8),
    ]

    kept = gate_releases(releases, query)

    assert len(kept) == 1
    assert kept[0].is_pack is True
    assert kept[0].pack_label == "S02"


def test_season_pack_query_labels_packs() -> None:
    query = KeywordQuery(title="Big Buck Bunny", season=2, pack=True)
    releases = [
        _release("Big.Buck.Bunny.S02.1080p.BluRay.mkv", seeders=9),
    ]

    kept = gate_releases(releases, query)

    assert [release.filename for release in kept] == ["Big.Buck.Bunny.S02.1080p.BluRay.mkv"]
    assert kept[0].is_pack is True
    assert kept[0].pack_label == "S02"


def test_aliases_participate_in_the_title_gate() -> None:
    query = KeywordQuery(
        title="Big Buck Bunny",
        year=2008,
        aliases=("Big Buck Bunny", "BBB"),
    )
    releases = [_release("BBB.2008.1080p.mkv")]

    kept = gate_releases(releases, query)

    assert len(kept) == 1


@pytest.mark.parametrize(
    "release",
    [
        _release("Big.Buck.Bunny.2008.1080p.mkv"),
        _release("Big.Buck.Bunny.S02E05.1080p.mkv"),
    ],
)
def test_gate_never_mutates_the_input_row(release: Release) -> None:
    query = KeywordQuery(title="Big Buck Bunny", season=2, episode=5)

    gate_releases([release], query)

    assert release.is_pack is False
    assert release.pack_label is None
