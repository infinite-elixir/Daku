from __future__ import annotations

import json
from collections.abc import Callable

from daku.services.torrents.animetosho import (
    PUBLIC_TRACKERS,
    build_magnet,
    build_search_url,
    matches_episode,
    matches_query,
    parse_feed,
)


def _fixture(load_fixture: Callable[[str], object]) -> str:
    return json.dumps(load_fixture("animetosho_feed.json"))


def test_parse_recorded_payload(load_fixture: Callable[[str], object]) -> None:
    releases = parse_feed(_fixture(load_fixture))

    # Missing hash, missing title and an invalid hash are dropped.
    assert len(releases) == 4
    first = releases[0]
    assert first.info_hash == "90970b8ee6ed88ece847cb97815560c493f41e39"
    assert first.filename.startswith("[Judas] Kore wa Zombie Desuka")
    assert first.quality == "1080p"
    assert first.size_bytes == 8_843_302_239
    assert first.seeders == 56
    assert first.provider == "animetosho"
    assert first.magnet.startswith("magnet:?xt=urn:btih:90970b8ee6ed88ece847cb97815560c493f41e39")
    assert first.metadata["category"] == "Nyaa"
    assert first.metadata["status"] == "complete"
    assert first.metadata["num_files"] == 25
    assert first.metadata["published_at"] == "2024-11-16T20:22:02+00:00"
    assert first.metadata["video_codec"] == "x265"


def test_magnet_is_built_locally_with_public_trackers() -> None:
    magnet = build_magnet("A" * 40, "Example & Show")

    assert magnet.startswith(f"magnet:?xt=urn:btih:{'a' * 40}&dn=Example%20%26%20Show")
    for tracker in PUBLIC_TRACKERS:
        assert "&tr=" in magnet
        assert tracker.replace(":", "%3A").replace("/", "%2F") in magnet


def test_optional_fields_degrade_to_defaults(load_fixture: Callable[[str], object]) -> None:
    releases = parse_feed(_fixture(load_fixture))
    bare = next(release for release in releases if release.filename == "Example Show - 02 [720p]")

    assert bare.filename == "Example Show - 02 [720p]"
    assert bare.size_bytes == 987_654
    assert bare.seeders == 0
    assert bare.metadata["published_at"] is None
    assert bare.metadata["status"] is None
    assert bare.metadata["num_files"] is None


def test_malformed_payload_never_raises() -> None:
    assert parse_feed("not json") == []
    assert parse_feed("{}") == []
    assert parse_feed("null") == []
    assert parse_feed(b'[{"info_hash": null, "title": null}]') == []


def test_html_entities_are_unescaped(load_fixture: Callable[[str], object]) -> None:
    releases = parse_feed(_fixture(load_fixture))

    assert releases[1].filename == "Zom 100 - 01 [1080p] & [Multi-Subs]"


def test_search_url_encodes_and_caps_query() -> None:
    assert build_search_url("Zom 100 & Zombie") == (
        "https://feed.animetosho.net/json?q=Zom+100+%26+Zombie"
    )
    capped = build_search_url("a" * 200)
    assert len(capped.split("q=", 1)[1]) == 120
    assert build_search_url("bad\x00query\n") == "https://feed.animetosho.net/json?q=bad+query"


def test_query_and_episode_matching() -> None:
    filename = "[Judas] Zom 100 - 03 [1080p][HEVC]"

    assert matches_query("Zom 100", filename)
    assert matches_query("Zombie", filename, ["Bucket List of the Dead"]) is False
    assert matches_query("Zombie", "[Group] Zombie - 01.mkv") is False
    assert matches_query(
        "Steel Ball Run",
        "[Judas] JoJo's Bizarre Adventure - Steel Ball Run (SBR) - 07 [1080p]",
    )
    assert matches_query("Steel Ball Run", "[Judas] SBR - 07 [1080p]")
    assert matches_query("Steel Ball Run", "[Judas] The Run - 07 [1080p]") is False
    assert matches_episode(filename, season=1, episode=3)
    assert matches_episode(filename, absolute=3)
    assert not matches_episode(filename, season=1, episode=4)
    assert matches_episode("[Group] Show 01-12 [1080p]", season=1, episode=4, pack=True)
