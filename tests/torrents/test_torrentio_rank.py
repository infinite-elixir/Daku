from __future__ import annotations

import pytest

from daku.services.torrents.base import Release
from daku.services.torrents.ranking import (
    detect_quality,
    filter_quality,
    parse_size_bytes,
    quality_rank,
    sort_releases,
)


def _release(
    name: str,
    *,
    quality: str = "1080p",
    seeders: int = 0,
    size: int | None = None,
) -> Release:
    return Release(
        info_hash=name,
        filename=f"{name}.mkv",
        quality=quality,
        size_bytes=size,
        seeders=seeders,
        provider="test",
        magnet=f"magnet:?xt=urn:btih:{name}",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2160p", "2160p"),
        ("BluRay 4K UHD", "2160p"),
        ("1440p WEB-DL", "1440p"),
        ("1080p BluRay", "1080p"),
        ("720p", "720p"),
        ("576p", "576p"),
        ("480p", "480p"),
        ("HDCAM", "cam"),
        ("CAMRIP", "cam"),
        ("DVDSCR", "scr"),
        ("Screener", "scr"),
        ("HDTS", "ts"),
        ("Telesync", "ts"),
        ("something else", "unknown"),
        ("", "unknown"),
    ],
)
def test_quality_labels_are_case_insensitive(text: str, expected: str) -> None:
    assert detect_quality(text) == expected
    assert detect_quality(text.upper()) == expected


def test_quality_ranks() -> None:
    assert quality_rank("2160p") == 60
    assert quality_rank("1440p") == 55
    assert quality_rank("1080p") == 50
    assert quality_rank("720p") == 40
    assert quality_rank("576p") == 35
    assert quality_rank("480p") == 30
    assert quality_rank("ts") == 4
    assert quality_rank("scr") == 5
    assert quality_rank("cam") == 3
    assert quality_rank("unknown") == 1
    assert quality_rank(None) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("209.37 MB", 219_540_357),
        ("1.5GiB", 1_610_612_736),
        ("700 KB", 716_800),
        ("2 TB", 2 * 1024**4),
        ("12345", 12_345),
        ("0", 0),
        (None, None),
        ("", None),
        ("no size", None),
    ],
)
def test_parse_size_bytes(value: str | None, expected: int | None) -> None:
    assert parse_size_bytes(value) == expected


def test_filter_quality_default_and_reenable() -> None:
    releases = [
        _release("a", quality="1080p"),
        _release("b", quality="cam"),
        _release("c", quality="scr"),
        _release("d", quality="720p"),
    ]

    assert [r.info_hash for r in filter_quality(releases, ["cam", "scr"])] == ["a", "d"]
    assert [r.info_hash for r in filter_quality(releases, [])] == ["a", "b", "c", "d"]
    assert [r.info_hash for r in filter_quality(releases, ["CAM"])] == ["a", "c", "d"]


def test_sort_seeders_desc_then_quality() -> None:
    releases = [
        _release("low", quality="2160p", seeders=1),
        _release("high", quality="720p", seeders=100),
        _release("high-better", quality="1080p", seeders=100),
    ]
    assert [r.info_hash for r in sort_releases(releases, "seeders")] == [
        "high-better",
        "high",
        "low",
    ]


def test_sort_quality_desc() -> None:
    releases = [
        _release("720", quality="720p", seeders=999),
        _release("2160", quality="2160p", seeders=1),
        _release("1080", quality="1080p", seeders=50),
    ]
    assert [r.info_hash for r in sort_releases(releases, "quality")] == ["2160", "1080", "720"]


def test_sort_size_desc_and_ties_are_stable() -> None:
    releases = [
        _release("first", seeders=1, size=100),
        _release("second", seeders=1, size=100),
        _release("large", seeders=0, size=999),
        _release("unknown", seeders=0, size=None),
    ]
    assert [r.info_hash for r in sort_releases(releases, "size")] == [
        "large",
        "first",
        "second",
        "unknown",
    ]


def test_unknown_sort_mode_falls_back_to_seeders() -> None:
    releases = [_release("low", seeders=1), _release("high", seeders=9)]
    assert [r.info_hash for r in sort_releases(releases, "bogus")] == ["high", "low"]
