from __future__ import annotations

import base64

import pytest

from daku.services.releases.magnet import MagnetError, parse_magnet

HEX_HASH = "0123456789abcdef0123456789abcdef01234567"
BASE32_HASH = base64.b32encode(bytes.fromhex(HEX_HASH)).decode("ascii")


def test_hex_hash_is_lowercased() -> None:
    parsed = parse_magnet(
        f"magnet:?xt=urn:btih:{HEX_HASH.upper()}"
        "&dn=Perfect.Blue.1997.1080p.BluRay.x264-GROUP.mkv"
        "&tr=udp%3A%2F%2Ftracker.one%3A80%2Fannounce"
        "&tr=udp%3A%2F%2Ftracker.two%3A6969%2Fannounce"
    )

    assert parsed.info_hash == HEX_HASH
    assert parsed.filename == "Perfect.Blue.1997.1080p.BluRay.x264-GROUP.mkv"
    assert parsed.trackers == [
        "udp://tracker.one:80/announce",
        "udp://tracker.two:6969/announce",
    ]
    assert parsed.provider == "manual"
    assert parsed.seeders is None
    assert parsed.quality == "1080p"
    assert parsed.metadata["source"] == "BluRay"
    assert parsed.metadata["video_codec"] == "x264"
    assert "dn=Perfect.Blue" in parsed.magnet
    assert parsed.magnet.count("&tr=") == 2


def test_base32_hash_converts_to_hex() -> None:
    parsed = parse_magnet(f"magnet:?xt=urn:btih:{BASE32_HASH}")
    assert parsed.info_hash == HEX_HASH
    assert len(parsed.info_hash) == 40


def test_dn_with_spaces_and_unicode() -> None:
    parsed = parse_magnet(
        "magnet:?xt=urn:btih:" + HEX_HASH + "&dn=Am%C3%A9lie%20%281997%29%20BluRay"
    )
    assert parsed.filename == "Amélie (1997) BluRay"


def test_missing_trackers_is_allowed() -> None:
    parsed = parse_magnet(f"magnet:?xt=urn:btih:{HEX_HASH}&dn=No.Trackers.mkv")
    assert parsed.trackers == []
    assert parsed.magnet == f"magnet:?xt=urn:btih:{HEX_HASH}&dn=No.Trackers.mkv"


def test_duplicate_trackers_are_deduplicated() -> None:
    tracker = "&tr=udp%3A%2F%2Ftracker.one%3A80%2Fannounce"
    parsed = parse_magnet(f"magnet:?xt=urn:btih:{HEX_HASH}{tracker}{tracker}")
    assert parsed.trackers == ["udp://tracker.one:80/announce"]


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://example.com/movie.torrent",
        "magnet:?dn=No.Hash.mkv&tr=udp%3A%2F%2Ftracker.one%3A80",
        "magnet:?xt=urn:btih:tooshort&dn=Bad.mkv",
        f"magnet:?xt=urn:btih:{'z' * 40}&dn=Bad.mkv",
        f"magnet:?xt=urn:btih:{'a' * 39}&dn=Bad.mkv",
    ],
)
def test_invalid_magnets_raise(uri: str) -> None:
    with pytest.raises(MagnetError):
        parse_magnet(uri)
