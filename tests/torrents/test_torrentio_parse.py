from __future__ import annotations

from collections.abc import Callable
from typing import Any

from daku.services.torrents.torrentio import parse_stream, parse_streams


def _load(load_fixture: Callable[[str], object]) -> dict[str, Any]:
    payload = load_fixture("torrentio_big_buck_bunny.json")
    assert isinstance(payload, dict)
    return payload


def test_parse_recorded_payload(load_fixture: Callable[[str], object]) -> None:
    releases = parse_streams(_load(load_fixture))

    # Three malformed/stream-less entries are dropped: missing hash, a string and
    # a null. The row without ``behaviorHints.filename`` is kept via the title
    # fallback.
    assert len(releases) == 7
    first = releases[0]
    assert first.info_hash == "abcdef0123456789abcdef0123456789abcdef01"
    assert first.filename == "Big.Buck.Bunny.2008.1080p.BluRay.x264.DDP5.1-GROUP.mkv"
    assert first.quality == "1080p"
    assert first.size_bytes == 219_540_357
    assert first.seeders == 43
    assert first.provider == "ThePirateBay"
    assert first.magnet.startswith("magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01")


def test_parse_metadata_hints(load_fixture: Callable[[str], object]) -> None:
    releases = parse_streams(_load(load_fixture))
    by_hash = {release.info_hash: release for release in releases}

    bluray = by_hash["abcdef0123456789abcdef0123456789abcdef01"]
    assert bluray.metadata == {
        "source": "BluRay",
        "video_codec": "x264",
        "audio_hint": "DDP",
        "languages": None,
        "release_group": "GROUP",
        "edition": None,
    }

    atmos = by_hash["1" * 40]
    assert atmos.metadata["source"] == "WEB-DL"
    assert atmos.metadata["video_codec"] == "x265"
    assert atmos.metadata["audio_hint"] == "Atmos"
    assert atmos.metadata["languages"] == ["multi"]
    assert atmos.metadata["release_group"] == "YIFY"

    webrip = by_hash["2" * 40]
    assert webrip.metadata["source"] == "WEBRip"
    assert webrip.metadata["audio_hint"] == "AAC"
    assert webrip.metadata["languages"] == ["en", "fr"]

    director = by_hash["4" * 40]
    assert director.quality == "1080p"
    assert director.size_bytes == int(3.1 * 1024**3)
    assert director.metadata["source"] == "BluRay"
    assert director.metadata["video_codec"] == "x265"
    assert director.metadata["audio_hint"] == "TrueHD"
    assert director.metadata["edition"] == "Director's Cut"


def test_missing_fields_are_null_not_guessed(load_fixture: Callable[[str], object]) -> None:
    releases = parse_streams(_load(load_fixture))
    bare = next(release for release in releases if release.info_hash == "5" * 40)

    assert bare.quality == "unknown"
    assert bare.size_bytes is None
    assert bare.seeders == 0
    assert bare.provider == "unknown"
    assert bare.metadata == {
        "source": None,
        "video_codec": None,
        "audio_hint": None,
        "languages": None,
        "release_group": None,
        "edition": None,
    }


def test_malformed_stream_is_skipped_never_raises() -> None:
    assert parse_stream(None) is None
    assert parse_stream("nope") is None
    assert parse_stream({"title": "missing hash"}) is None
    assert parse_stream({"infoHash": "  "}) is None
    assert parse_streams({"streams": "not a list"}) == []
    assert parse_streams(None) == []


def test_filename_falls_back_to_title_then_hash() -> None:
    title_row = parse_stream(
        {
            "infoHash": "a" * 40,
            "title": "Torrentio\n1080p\nBig Buck Bunny (2008)\n👤 10 💾 1 GB ⚙️ yts",
        }
    )
    assert title_row is not None
    assert title_row.filename == "Torrentio 1080p Big Buck Bunny (2008)"

    hash_row = parse_stream({"infoHash": "b" * 40})
    assert hash_row is not None
    assert hash_row.filename == f"release-{'b' * 12}"


def test_non_ascii_and_multiline_titles(load_fixture: Callable[[str], object]) -> None:
    releases = parse_streams(_load(load_fixture))
    amelie = next(release for release in releases if release.info_hash == "4" * 40)

    assert "Amélie" in amelie.filename
    assert "Am%C3%A9lie" in amelie.magnet
    assert "\n" not in amelie.filename
