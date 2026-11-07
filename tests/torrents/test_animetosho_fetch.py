from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import respx

from daku.services.torrents import animetosho
from daku.services.torrents import throttle as throttle_module
from daku.services.torrents.animetosho import (
    FEED_BASE,
    LEGACY_FEED_BASE,
    AnimeToshoError,
    build_search_url,
    fetch_releases,
)
from daku.services.torrents.cache import PayloadCache


def _fixture(load_fixture: Callable[[str], object]) -> str:
    return json.dumps(load_fixture("animetosho_feed.json"))


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(throttle_module, "_last_request", {})


@pytest.mark.asyncio
async def test_repeated_search_reuses_cache(load_fixture: Callable[[str], object]) -> None:
    url = build_search_url("Zom 100")
    cache = PayloadCache()
    payload = _fixture(load_fixture)

    with respx.mock() as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, text=payload))
        async with httpx.AsyncClient() as client:
            first = await fetch_releases(client, cache, "Zom 100")
            second = await fetch_releases(client, cache, "Zom 100")

    assert route.call_count == 1
    assert first.stale is False
    assert second.stale is False
    assert second.cached_at == first.cached_at
    assert len(second.releases) == 4


@pytest.mark.asyncio
async def test_upstream_failure_serves_stale_payload(
    load_fixture: Callable[[str], object],
) -> None:
    url = build_search_url("Zom 100")
    cache = PayloadCache()
    payload = _fixture(load_fixture)

    with respx.mock() as mock:
        route = mock.get(url)
        route.side_effect = [
            httpx.Response(200, text=payload),
            httpx.Response(500),
        ]
        async with httpx.AsyncClient() as client:
            first = await fetch_releases(client, cache, "Zom 100")
            cache.ttl_seconds = -1
            second = await fetch_releases(client, cache, "Zom 100")

    assert first.stale is False
    assert second.stale is True
    assert second.cached_at == first.cached_at
    assert [row.info_hash for row in second.releases] == [row.info_hash for row in first.releases]
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_rate_limit_without_cache_raises() -> None:
    url = build_search_url("Zom 100")

    with respx.mock() as mock:
        mock.get(url).mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as client:
            with pytest.raises(AnimeToshoError):
                await fetch_releases(client, PayloadCache(), "Zom 100")


@pytest.mark.asyncio
async def test_oversized_payload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    url = build_search_url("Zom 100")
    monkeypatch.setattr(animetosho, "MAX_PAYLOAD_BYTES", 1)

    with respx.mock() as mock:
        mock.get(url).mock(return_value=httpx.Response(200, text="[]"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(AnimeToshoError, match="response too large"):
                await fetch_releases(client, PayloadCache(), "Zom 100")


@pytest.mark.asyncio
async def test_legacy_xyz_cache_entry_still_resolves(
    load_fixture: Callable[[str], object],
) -> None:
    url = build_search_url("Zom 100")
    legacy = url.replace(FEED_BASE, LEGACY_FEED_BASE, 1)
    cache = PayloadCache()
    cache.set(legacy, _fixture(load_fixture))

    with respx.mock() as mock:
        async with httpx.AsyncClient() as client:
            result = await fetch_releases(client, cache, "Zom 100")
        assert not mock.calls

    assert result.stale is False
    assert len(result.releases) == 4
