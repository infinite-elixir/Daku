"""Per-source outbound request throttle.

One lock per event loop because tests create a fresh loop per ``TestClient``; each
source uses its own key, so a slow anime feed never delays a movie index.
"""

from __future__ import annotations

import asyncio
import time

_last_request: dict[str, float] = {}
_locks: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Lock] = {}


def _lock(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    bucket = (loop, key)
    lock = _locks.get(bucket)
    if lock is None:
        lock = asyncio.Lock()
        _locks[bucket] = lock
    return lock


async def throttle(key: str, interval: float = 1.0) -> None:
    """Wait until at least ``interval`` seconds have passed for ``key``."""

    async with _lock(key):
        wait = interval - (time.monotonic() - _last_request.get(key, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request[key] = time.monotonic()
