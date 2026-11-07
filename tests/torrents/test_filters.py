from __future__ import annotations

from daku.services.torrents.base import Release
from daku.services.torrents.ranking import (
    apply_min_quality,
    dedupe_releases,
    filter_quality,
    is_high_quality_unlabelled,
    quality_counts,
)


def _release(
    info_hash: str,
    *,
    quality: str = "1080p",
    seeders: int = 1,
    provider: str = "yts",
) -> Release:
    return Release(
        info_hash=info_hash,
        filename=f"{info_hash}.{quality}.mkv",
        quality=quality,
        size_bytes=1024,
        seeders=seeders,
        provider=provider,
        magnet=f"magnet:?xt=urn:btih:{info_hash}",
    )


def test_min_quality_keeps_at_or_above_and_excludes_unknown() -> None:
    rows = [
        _release("a", quality="2160p"),
        _release("b", quality="1440p"),
        _release("c", quality="1080p"),
        _release("d", quality="720p"),
        _release("e", quality="unknown"),
        _release("f", quality="cam"),
    ]

    filtered = apply_min_quality(rows, "1080p")

    assert [row.quality for row in filtered] == ["2160p", "1440p", "1080p"]


def test_no_min_quality_keeps_everything() -> None:
    rows = [_release("a", quality="unknown"), _release("b", quality="cam")]
    assert apply_min_quality(rows, None) == rows


def test_dedupe_keeps_highest_seeders_and_preserves_first_position() -> None:
    rows = [
        _release("AAAA", seeders=10, provider="yts"),
        _release("bbbb", seeders=5),
        _release("aaaa", seeders=42, provider="eztv"),
        _release("BbBb", seeders=20),
    ]

    kept, removed = dedupe_releases(rows)

    assert removed == 2
    assert [row.info_hash.lower() for row in kept] == ["aaaa", "bbbb"]
    assert kept[0].seeders == 42
    assert kept[0].provider == "eztv"
    assert kept[1].seeders == 20


def test_quality_counts_are_ordered_and_match_filtered_rows() -> None:
    rows = [
        _release("a", quality="1080p"),
        _release("b", quality="2160p"),
        _release("c", quality="1080p"),
        _release("d", quality="720p"),
    ]
    filtered = filter_quality(rows, ["2160p"])

    counts = quality_counts(filtered)

    assert counts == {"1080p": 2, "720p": 1}
    assert list(counts) == ["1080p", "720p"]


def test_unlabelled_remux_is_high_quality() -> None:
    remux = Release(
        info_hash="r",
        filename="Movie.2019.REMUX.AVC.mkv",
        quality="unknown",
        size_bytes=1,
        seeders=1,
        provider="yts",
        magnet="magnet:?xt=urn:btih:r",
    )
    junk = _release("j", quality="unknown")

    assert is_high_quality_unlabelled(remux) is True
    assert is_high_quality_unlabelled(junk) is False


def test_unknown_filter_keeps_packs_and_remuxes_only() -> None:
    pack = Release(
        info_hash="pack",
        filename="Show.S01.Batch.mkv",
        quality="unknown",
        size_bytes=1,
        seeders=1,
        provider="yts",
        magnet="magnet:?xt=urn:btih:pack",
        is_pack=True,
    )
    remux = Release(
        info_hash="remux",
        filename="Movie.2019.REMUX.mkv",
        quality="unknown",
        size_bytes=1,
        seeders=1,
        provider="yts",
        magnet="magnet:?xt=urn:btih:remux",
    )
    plain = _release("plain", quality="unknown")

    kept = filter_quality([pack, remux, plain], ["unknown"])

    assert [row.info_hash for row in kept] == ["pack", "remux"]
