"""Shared HTTP behaviour for metadata upstreams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from daku import __version__
from daku.services.metadata.cache import TTLCache

USER_AGENT = f"daku/{__version__}"
REQUEST_TIMEOUT = 5.0
REQUEST_BUDGET = 8.0
RETRIES = 1
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

Sender = Callable[[float], Awaitable[httpx.Response]]


class UpstreamError(Exception):
    """An upstream request failed after retries."""

    def __init__(self, url: str, detail: str, status_code: int | None = None) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail
        self.status_code = status_code


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=True,
    )


def request_cache_key(url: str, body: Any | None = None) -> str:
    """Cache key for a request. POST bodies share a URL, so hash the body."""

    if body is None:
        return url
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{url}#{hashlib.sha256(encoded).hexdigest()[:16]}"


async def _request_json(
    cache: TTLCache,
    key: str,
    send: Sender,
    url: str,
    *,
    deadline: float | None,
) -> Any:
    fresh = cache.get_fresh(key)
    if fresh is not None:
        return fresh.value

    budget_end = deadline if deadline is not None else time.monotonic() + REQUEST_BUDGET
    last_error: UpstreamError | None = None

    for attempt in range(RETRIES + 1):
        remaining = budget_end - time.monotonic()
        if remaining <= 0.05:
            break
        try:
            response = await send(min(REQUEST_TIMEOUT, remaining))
        except httpx.HTTPError as exc:
            last_error = UpstreamError(url, f"request failed: {exc}")
        else:
            if 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    last_error = UpstreamError(url, f"invalid JSON: {exc}", response.status_code)
                else:
                    cache.set(key, payload)
                    return payload
            else:
                last_error = UpstreamError(
                    url, f"HTTP {response.status_code}", response.status_code
                )
                if response.status_code not in RETRYABLE_STATUS:
                    break

        if attempt < RETRIES:
            pause = min(0.2, max(0.0, budget_end - time.monotonic() - 0.05))
            if pause:
                await asyncio.sleep(pause)

    raise last_error or UpstreamError(url, "request budget exhausted")


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    cache: TTLCache,
    *,
    deadline: float | None = None,
    cache_key: str | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    """Fetch and cache a JSON document with GET.

    Fresh cache hits never touch the network. Non-2xx responses and invalid JSON are
    never cached. One retry is attempted within an 8 s overall budget.
    """

    async def send(timeout: float) -> httpx.Response:
        return await client.get(url, params=params, timeout=timeout)

    key = cache_key or (url if params is None else request_cache_key(url, params))
    return await _request_json(cache, key, send, url, deadline=deadline)


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    body: Any,
    cache: TTLCache,
    *,
    deadline: float | None = None,
    cache_key: str | None = None,
) -> Any:
    """Fetch and cache a JSON document with a JSON POST (e.g. GraphQL)."""

    async def send(timeout: float) -> httpx.Response:
        return await client.post(url, json=body, timeout=timeout)

    return await _request_json(
        cache,
        cache_key or request_cache_key(url, body),
        send,
        url,
        deadline=deadline,
    )


def cached_payload(cache: TTLCache, url: str) -> Any | None:
    entry = cache.get_stale(url)
    return entry.value if entry is not None else None
