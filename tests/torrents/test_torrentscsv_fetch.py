from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
import respx

from daku.services.torrents import throttle as throttle_module
from daku.services.torrents.base import KeywordQuery
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.throttle import throttle
from daku.services.torrents.torrentscsv import (
    MAX_PAYLOAD_BYTES,
    TorrentsCsvError,
    build_search_url,
    fetch_releases,
)

QUERY = KeywordQuery(title="Big Buck Bunny", year=2008)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the per-source throttle and keep tests from sleeping."""

    monkeypatch.setattr(throttle_module, "_last_request", {})
    ticks = iter(range(1000, 1_000_000, 2))
    monkeypatch.setattr(throttle_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))


def _fixture(load_fixture: Callable[[str], object]) -> str:
    return json.dumps(load_fixture("torrents_csv_search.json"))


@pytest.mark.asyncio
async def test_repeated_search_reuses_cache(load_fixture: Callable[[str], object]) -> None:
    url = build_search_url("Big Buck Bunny 2008")
    cache = PayloadCache()
    payload = _fixture(load_fixture)

    with respx.mock() as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, text=payload))
        async with httpx.AsyncClient() as client:
            first = await fetch_releases(client, cache, QUERY)
            second = await fetch_releases(client, cache, QUERY)

    assert route.call_count == 1
    assert first.stale is False
    assert second.stale is False
    assert second.cached_at == first.cached_at
    # Only the 2008 movie survives the title/year gate.
    assert [release.info_hash for release in second.releases] == ["a" * 40]


@pytest.mark.asyncio
async def test_upstream_failure_serves_stale_payload(
    load_fixture: Callable[[str], object],
) -> None:
    url = build_search_url("Big Buck Bunny 2008")
    cache = PayloadCache()
    payload = _fixture(load_fixture)

    with respx.mock() as mock:
        route = mock.get(url)
        route.side_effect = [
            httpx.Response(200, text=payload),
            httpx.Response(500),
        ]
        async with httpx.AsyncClient() as client:
            first = await fetch_releases(client, cache, QUERY)
            cache.ttl_seconds = -1
            second = await fetch_releases(client, cache, QUERY)

    assert first.stale is False
    assert second.stale is True
    assert second.cached_at == first.cached_at
    assert [row.info_hash for row in second.releases] == [row.info_hash for row in first.releases]
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_http_error_without_cache_raises() -> None:
    url = build_search_url("Big Buck Bunny 2008")

    with respx.mock() as mock:
        mock.get(url).mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as client:
            with pytest.raises(TorrentsCsvError):
                await fetch_releases(client, PayloadCache(), QUERY)


@pytest.mark.asyncio
async def test_invalid_json_raises() -> None:
    url = build_search_url("Big Buck Bunny 2008")

    with respx.mock() as mock:
        mock.get(url).mock(return_value=httpx.Response(200, text="not json"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(TorrentsCsvError):
                await fetch_releases(client, PayloadCache(), QUERY)


@pytest.mark.asyncio
async def test_oversized_payload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    url = build_search_url("Big Buck Bunny 2008")
    monkeypatch.setattr("daku.services.torrents.torrentscsv.MAX_PAYLOAD_BYTES", 1)

    with respx.mock() as mock:
        mock.get(url).mock(return_value=httpx.Response(200, text="[]"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(TorrentsCsvError, match="response too large"):
                await fetch_releases(client, PayloadCache(), QUERY)


@pytest.mark.asyncio
async def test_sends_a_descriptive_user_agent() -> None:
    url = build_search_url("Big Buck Bunny 2008")

    with respx.mock() as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=[]))
        async with httpx.AsyncClient() as client:
            await fetch_releases(client, PayloadCache(), QUERY)

    assert route.calls[0].request.headers["user-agent"].startswith("daku/")


@pytest.mark.asyncio
async def test_one_query_per_search() -> None:
    url = build_search_url("Big Buck Bunny 2008")

    with respx.mock() as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=[]))
        async with httpx.AsyncClient() as client:
            result = await fetch_releases(client, PayloadCache(), QUERY)

    assert route.call_count == 1
    assert result.queries == 1


@pytest.mark.asyncio
async def test_throttle_enforces_one_request_per_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    ticks = iter([100.0, 100.0, 100.4, 100.4, 101.0])
    monkeypatch.setattr(throttle_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(throttle_module, "_last_request", {})

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(throttle_module.asyncio, "sleep", fake_sleep)

    await throttle("torrents-csv")
    await throttle("torrents-csv")

    assert len(waits) == 1
    assert waits[0] == pytest.approx(0.6)


def test_limits_match_the_contract() -> None:
    from daku.services.torrents import torrentscsv

    assert MAX_PAYLOAD_BYTES == 8 * 1024 * 1024
    assert torrentscsv.SEARCH_DEADLINE_SECONDS == 8.0
    assert torrentscsv.MAX_ROWS == 100
