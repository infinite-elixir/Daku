from __future__ import annotations

from daku.services.torrents.registry import (
    SOURCES,
    available_sources,
    get_source,
    is_known_source,
)


def test_available_sources_match_the_media_kind() -> None:
    assert available_sources("movie") == ("torrentio", "torrents-csv")
    assert available_sources("series") == ("torrentio", "torrents-csv")
    assert available_sources("anime") == ("torrentio", "animetosho")


def test_unknown_or_wrong_kind_is_not_known() -> None:
    assert is_known_source("torrentio", "movie") is True
    assert is_known_source("animetosho", "movie") is False
    assert is_known_source("torrents-csv", "anime") is False
    assert is_known_source("junk", "movie") is False


def test_capability_flags() -> None:
    torrentio = SOURCES["torrentio"]
    assert torrentio.requires_imdb is True
    assert torrentio.keyword is False

    keyword = get_source("torrents-csv")
    assert keyword is not None
    assert keyword.requires_imdb is False
    assert keyword.keyword is True
