from __future__ import annotations

import httpx
import pytest
import respx

from daku.config import TORRENTIO_DEFAULT
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.torrentio import (
    build_stream_url,
    fetch_releases,
    with_providers,
)


def test_with_providers_replaces_existing_part_in_place() -> None:
    config = "providers=yts,eztv|qualityfilter=scr,cam,unknown|sort=seeders"

    assert with_providers(config, ["1337x", "YTS", "yts"]) == (
        "providers=1337x,yts|qualityfilter=scr,cam,unknown|sort=seeders"
    )


def test_with_providers_adds_prefix_when_missing() -> None:
    assert with_providers("qualityfilter=scr|sort=seeders", ["yts"]) == (
        "providers=yts|qualityfilter=scr|sort=seeders"
    )


def test_with_providers_empty_list_keeps_configured_default() -> None:
    config = "providers=yts|sort=seeders"

    assert with_providers(config, []) == config
    assert with_providers(config, ["  "]) == config
    assert with_providers("", []) == ""


def test_shipped_default_sends_no_providers_or_qualityfilter() -> None:
    assert TORRENTIO_DEFAULT == "sort=seeders"

    url = build_stream_url(TORRENTIO_DEFAULT, "movie", "tt1")

    assert url == "https://torrentio.strem.fun/sort=seeders/stream/movie/tt1.json"
    assert "providers=" not in url
    assert "qualityfilter=" not in url


def test_provider_selection_changes_the_stream_url() -> None:
    one = build_stream_url(with_providers("", ["yts"]), "movie", "tt1")
    two = build_stream_url(with_providers("", ["eztv"]), "movie", "tt1")

    assert one != two
    assert "/providers=yts/" in one
    assert "/providers=eztv/" in two


@pytest.mark.asyncio
async def test_different_provider_selections_use_separate_cache_entries() -> None:
    cache = PayloadCache()
    one = build_stream_url("providers=yts", "movie", "tt1")
    two = build_stream_url("providers=eztv", "movie", "tt1")

    with respx.mock(assert_all_called=True) as mock:
        mock.get(one).mock(return_value=httpx.Response(200, json={"streams": []}))
        mock.get(two).mock(return_value=httpx.Response(200, json={"streams": []}))
        async with httpx.AsyncClient() as client:
            await fetch_releases(client, cache, "providers=yts", "movie", "tt1")
            await fetch_releases(client, cache, "providers=eztv", "movie", "tt1")

    assert len(cache) == 2

    # Switching back reuses the first entry instead of refetching.
    with respx.mock(assert_all_called=False) as mock:
        async with httpx.AsyncClient() as client:
            outcome = await fetch_releases(client, cache, "providers=yts", "movie", "tt1")
        assert not mock.calls

    assert outcome.stale is False
