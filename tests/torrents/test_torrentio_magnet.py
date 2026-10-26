from __future__ import annotations

from daku.services.torrents.torrentio import (
    build_magnet,
    build_prefix,
    build_stream_url,
    normalise_prefix,
    sanitize_filename,
    with_qualityfilter,
)

TRACKER_A = "udp://tracker.opentrackr.org:1337/announce"
TRACKER_B = "https://tracker.example.org/announce?pass=a b"


def test_movie_and_episode_urls() -> None:
    assert (
        build_stream_url("", "movie", "tt1254207")
        == "https://torrentio.strem.fun/stream/movie/tt1254207.json"
    )
    assert (
        build_stream_url(None, "series", "tt0944947:2:5")
        == "https://torrentio.strem.fun/stream/series/tt0944947:2:5.json"
    )


def test_prefix_is_only_added_when_non_empty() -> None:
    assert normalise_prefix("") == ""
    assert normalise_prefix("  |  ") == ""
    assert (
        build_stream_url("  ", "movie", "tt1")
        == "https://torrentio.strem.fun/stream/movie/tt1.json"
    )
    assert build_stream_url("providers=yts", "movie", "tt1") == (
        "https://torrentio.strem.fun/providers=yts/stream/movie/tt1.json"
    )
    # Stray separators must not produce `//`.
    assert build_stream_url("|providers=yts||sort=seeders|", "movie", "tt1") == (
        "https://torrentio.strem.fun/providers=yts|sort=seeders/stream/movie/tt1.json"
    )


def test_build_prefix_joins_parts_like_tsearch() -> None:
    assert (
        build_prefix(providers="yts,eztv", qualityfilter="scr,cam", sort="seeders")
        == "providers=yts,eztv|qualityfilter=scr,cam|sort=seeders"
    )
    assert build_prefix(providers="yts", qualityfilter="", sort=None) == "providers=yts"
    assert build_prefix() == ""


def test_qualityfilter_setting_drives_the_upstream_prefix() -> None:
    base = "providers=yts,eztv|qualityfilter=scr,cam|sort=seeders"
    assert with_qualityfilter(base, ["cam", "scr"]) == base
    assert with_qualityfilter(base, []) == "providers=yts,eztv|sort=seeders"
    assert with_qualityfilter(base, ["cam"]) == "providers=yts,eztv|qualityfilter=cam|sort=seeders"
    assert with_qualityfilter("providers=yts", ["scr"]) == "providers=yts"
    assert with_qualityfilter("", []) == ""


def test_magnet_has_dn_and_every_unique_tracker() -> None:
    magnet = build_magnet(
        "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
        "Big Buck Bunny.mkv",
        [
            f"tracker:{TRACKER_A}",
            "dht:abcdef",
            f"tracker:{TRACKER_A}",
            f"tracker:{TRACKER_B}",
            "not-a-source",
        ],
    )

    assert magnet == (
        "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01"
        "&dn=Big%20Buck%20Bunny.mkv"
        f"&tr={TRACKER_A.replace(':', '%3A').replace('/', '%2F')}"
        "&tr=https%3A%2F%2Ftracker.example.org%2Fannounce%3Fpass%3Da%20b"
    )
    assert "dht" not in magnet


def test_magnet_required_fields() -> None:
    assert build_magnet(None, "name.mkv", []) is None
    assert build_magnet("abc", None, []) is None
    assert build_magnet("abc", "  ", []) is None


def test_magnet_without_trackers_is_valid() -> None:
    assert (
        build_magnet("abcdef0123456789abcdef0123456789abcdef01", "a.mkv", None)
        == "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01&dn=a.mkv"
    )


def test_sanitize_filename_strips_paths_and_controls() -> None:
    assert sanitize_filename("a/b\\c\nname.mkv") == "a_b_c name.mkv"
    assert sanitize_filename("\x00") is None
    assert sanitize_filename("  ") is None
