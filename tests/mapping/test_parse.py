from __future__ import annotations

from typing import Any

import pytest

from daku.services.metadata import mapping
from daku.services.metadata.mapping import Descriptor


def test_parse_descriptor_forms() -> None:
    assert mapping.parse_descriptor("anilist:154587") == Descriptor("anilist", "154587", "")
    assert mapping.parse_descriptor("imdb_show:tt1234567:s1") == Descriptor(
        "imdb_show", "tt1234567", "s1"
    )
    assert mapping.parse_descriptor("tvdb_show:600:s0") == Descriptor("tvdb_show", "600", "s0")


def test_parse_descriptor_rejects_unknown_or_malformed() -> None:
    assert mapping.parse_descriptor("notaprovider:1") is None
    assert mapping.parse_descriptor("anilist:") is None
    assert mapping.parse_descriptor("anilist") is None
    assert mapping.parse_descriptor("") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", (1, 1)),
        ("5", (5, 5)),
        ("1-12", (1, 12)),
        ("13-", (13, None)),
        ("1-1", (1, 1)),
    ],
)
def test_parse_source_range_forms(raw: str, expected: tuple[int, int | None]) -> None:
    assert mapping.parse_source_range(raw) == expected


@pytest.mark.parametrize("raw", ["0", "0-3", "5-2", "-1", "1-x", "", "1-2-3"])
def test_parse_source_range_rejects_invalid(raw: str) -> None:
    with pytest.raises(mapping.MappingError):
        mapping.parse_source_range(raw)


@pytest.mark.parametrize(
    ("raw", "segments", "ratio"),
    [
        ("1", [(1, 1)], 1),
        ("1-12", [(1, 12)], 1),
        ("13-", [(13, None)], 1),
        ("1-6,8-13", [(1, 6), (8, 13)], 1),
        ("1-3,5-6,9-", [(1, 3), (5, 6), (9, None)], 1),
        ("14-|2", [(14, None)], 2),
        ("1-6,8-13|2", [(1, 6), (8, 13)], 2),
        ("1-2|-3", [(1, 2)], -3),
    ],
)
def test_parse_target_ranges_forms(
    raw: str, segments: list[tuple[int, int | None]], ratio: int
) -> None:
    assert mapping.parse_target_ranges(raw) == (segments, ratio)


@pytest.mark.parametrize("raw", ["1|x", "|2", "1-2,", "5-1", ""])
def test_parse_target_ranges_rejects_invalid(raw: str) -> None:
    with pytest.raises(mapping.MappingError):
        mapping.parse_target_ranges(raw)


def test_parse_target_ranges_normalises_zero_ratio() -> None:
    # ``|0`` appears in the published dataset on a few one-to-one movie entries.
    assert mapping.parse_target_ranges("1|0") == ([(1, 1)], 1)


def test_flatten_splits_multi_segment_target(mapping_payload: dict[str, Any]) -> None:
    rows = mapping.flatten_mappings(mapping_payload)
    tmdb = [
        row for row in rows if row.src_provider == "tmdb_show" and row.tgt_provider == "anilist"
    ]

    # "1-12": "1-6,8-13" is split into two source sub-ranges so target 7 stays skipped.
    assert (1, 6, 1, 6) in [(r.src_start, r.src_end, r.tgt_start, r.tgt_end) for r in tmdb]
    assert (7, 12, 8, 13) in [(r.src_start, r.src_end, r.tgt_start, r.tgt_end) for r in tmdb]
    # "13-": "14-|2" keeps the ratio on an open-ended range.
    open_row = next(r for r in tmdb if r.src_start == 13)
    assert open_row.src_end == 0
    assert open_row.tgt_start == 14
    assert open_row.tgt_end == 0
    assert open_row.ratio == 2
    # no row ever maps source 7 onto target 7
    assert not any(r.tgt_start <= 7 <= r.tgt_end for r in tmdb if r.src_start == 7)


def test_flatten_negative_ratio_groups_source_episodes(mapping_payload: dict[str, Any]) -> None:
    rows = mapping.flatten_mappings(mapping_payload)
    tvdb = [
        row for row in rows if row.src_provider == "tvdb_show" and row.tgt_provider == "anilist"
    ]

    assert len(tvdb) == 1
    row = tvdb[0]
    assert (row.src_start, row.src_end) == (1, 6)
    assert (row.tgt_start, row.tgt_end) == (1, 2)
    assert row.ratio == -3


def test_flatten_builds_reverse_rows_when_absent(mapping_payload: dict[str, Any]) -> None:
    rows = mapping.flatten_mappings(mapping_payload)
    reverse = [
        row
        for row in rows
        if row.src_provider == "anilist"
        and row.src_id == "154587"
        and row.tgt_provider == "imdb_show"
        and row.tgt_scope == "s1"
    ]

    assert [(r.src_start, r.src_end, r.tgt_start, r.tgt_end) for r in reverse] == [
        (1, 12, 1, 12),
        (13, 0, 13, 0),
    ]
    assert all(abs(r.ratio) == 1 for r in reverse)
    # The dataset only has anilist -> mal, so mal -> anilist is synthesised.
    mal_reverse = [
        row for row in rows if row.src_provider == "mal" and row.tgt_provider == "anilist"
    ]
    assert len(mal_reverse) == 1
    assert (mal_reverse[0].src_id, mal_reverse[0].tgt_id) == ("52991", "154587")


def test_flatten_does_not_duplicate_explicit_reverse() -> None:
    payload = {
        "$meta": {"schema_version": "3.0.0"},
        "anilist:1": {"imdb_show:tt1:s1": {"1-3": "1-3"}},
        "imdb_show:tt1:s1": {"anilist:1": {"1-3": "1-3"}},
    }

    rows = mapping.flatten_mappings(payload)

    forward = [r for r in rows if r.src_provider == "anilist"]
    reverse = [r for r in rows if r.src_provider == "imdb_show"]
    assert len(forward) == 1
    assert len(reverse) == 1


def test_dataset_meta_validation(mapping_payload: dict[str, Any]) -> None:
    meta = mapping.dataset_meta(mapping_payload)
    assert meta == {"schema_version": "3.0.3", "generated_on": "2026-10-23T06:49:48Z"}

    with pytest.raises(mapping.MappingError) as excinfo:
        mapping.dataset_meta({"$meta": {"schema_version": "2.0.0"}, "anilist:1": {}})
    assert excinfo.value.code == "MAPPING_UNSUPPORTED_SCHEMA"

    with pytest.raises(mapping.MappingError):
        mapping.dataset_meta({"$meta": {}, "anilist:1": {}})
    with pytest.raises(mapping.MappingError):
        mapping.dataset_meta({"$meta": {"schema_version": "3.0.0"}})
    with pytest.raises(mapping.MappingError):
        mapping.dataset_meta([])
