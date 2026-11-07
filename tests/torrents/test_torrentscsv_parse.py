from __future__ import annotations

from collections.abc import Callable
from typing import Any

from daku.services.torrents.magnets import PUBLIC_TRACKERS
from daku.services.torrents.torrentscsv import (
    MAX_ROWS,
    build_magnet,
    parse_results,
)


def _fixture(load_fixture: Callable[[str], object]) -> object:
    return load_fixture("torrents_csv_search.json")


def _row(
    *,
    info_hash: str = "a" * 40,
    name: str = "Big.Buck.Bunny.2008.1080p.mkv",
    size_bytes: int = 1_000_000_000,
    seeders: int = 10,
) -> dict[str, Any]:
    return {
        "infohash": info_hash,
        "name": name,
        "size_bytes": size_bytes,
        "seeders": seeders,
    }


def test_parse_recorded_payload(load_fixture: Callable[[str], object]) -> None:
    releases = parse_results(_fixture(load_fixture))

    # Invalid hash, empty name and zero size are dropped.
    assert len(releases) == 3
    first = releases[0]
    assert first.info_hash == "a" * 40
    assert first.filename == "Big.Buck.Bunny.2008.2160p.WEB-DL.DDP5.1-GROUP.mkv"
    assert first.quality == "2160p"
    assert first.size_bytes == 5_000_000_000
    assert first.seeders == 120
    assert first.provider == "torrents-csv"
    assert first.magnet.startswith(f"magnet:?xt=urn:btih:{'a' * 40}&dn=")
    assert first.metadata["leechers"] == 4
    assert first.metadata["completed"] == 51
    assert first.metadata["published_at"] == "2023-11-14T22:13:20+00:00"
    assert first.metadata["source"] == "WEB-DL"
    assert first.is_pack is False


def test_rows_are_sorted_by_seeders_descending(
    load_fixture: Callable[[str], object],
) -> None:
    releases = parse_results(_fixture(load_fixture))

    assert [release.seeders for release in releases] == [120, 40, 20]


def test_magnet_is_built_locally_with_public_trackers() -> None:
    magnet = build_magnet("A" * 40, "Example & Show")

    assert magnet.startswith(f"magnet:?xt=urn:btih:{'a' * 40}&dn=Example%20%26%20Show")
    for tracker in PUBLIC_TRACKERS:
        assert "&tr=" in magnet
        assert tracker.replace(":", "%3A").replace("/", "%2F") in magnet


def test_malformed_rows_are_dropped() -> None:
    payload = [
        _row(info_hash="not-a-hash"),
        _row(info_hash=""),
        _row(name=""),
        _row(name="   "),
        _row(size_bytes=0),
        _row(size_bytes=-1),
        "not a row",
        _row(info_hash="b" * 40),
    ]

    releases = parse_results(payload)

    assert [release.info_hash for release in releases] == ["b" * 40]


def test_uppercase_hash_is_normalised_and_zero_seeders_kept() -> None:
    releases = parse_results([_row(info_hash="A" * 40, seeders=0)])

    assert len(releases) == 1
    assert releases[0].info_hash == "a" * 40
    assert releases[0].seeders == 0
    assert "magnet:?xt=urn:btih:aaaaaaaa" in releases[0].magnet


def test_optional_fields_default_to_zero_and_none() -> None:
    releases = parse_results([_row()])

    assert len(releases) == 1
    metadata = releases[0].metadata
    assert metadata["leechers"] == 0
    assert metadata["completed"] == 0
    assert metadata["published_at"] is None


def test_numeric_strings_are_accepted() -> None:
    releases = parse_results(
        [
            {
                "infohash": "a" * 40,
                "name": "Show.2020.720p.mkv",
                "size_bytes": "2048",
                "seeders": "17",
            }
        ]
    )

    assert releases[0].size_bytes == 2048
    assert releases[0].seeders == 17


def test_malformed_payload_never_raises() -> None:
    assert parse_results("not json") == []
    assert parse_results("{}") == []
    assert parse_results(None) == []
    assert parse_results({}) == []
    assert parse_results({"results": "nope"}) == []


def test_dict_envelopes_are_read() -> None:
    row = _row(info_hash="c" * 40)

    assert len(parse_results({"results": [row]})) == 1
    assert len(parse_results({"torrents": [row]})) == 1
    assert len(parse_results({"data": [row]})) == 1


def test_result_cap_is_applied_after_sorting() -> None:
    payload = [
        _row(info_hash=f"{index:040x}", name=f"Show.{index:03d}.mkv", seeders=index)
        for index in range(1, MAX_ROWS + 50)
    ]

    releases = parse_results(payload)

    assert len(releases) == MAX_ROWS
    assert releases[0].seeders == MAX_ROWS + 49
    assert releases[-1].seeders == 50
