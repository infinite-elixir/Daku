"""Short-lived cache for raw Torrentio payloads.

The *payload* is cached, not the parsed release list: changing sort order or the
quality filter must not trigger another upstream request. Expired entries remain
readable so an outage can be served stale with an explicit flag.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from daku.utils import utc_now_iso

DEFAULT_TTL_SECONDS = 3600.0


@dataclass(slots=True)
class CachedPayload:
    payload: Any
    stored_at: float
    cached_at: str

    def age(self, now: float) -> float:
        return now - self.stored_at


class PayloadCache:
    """In-process, monotonic-clock TTL cache keyed by upstream URL."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._entries: dict[str, CachedPayload] = {}

    def set(self, key: str, payload: Any) -> CachedPayload:
        entry = CachedPayload(
            payload=payload,
            stored_at=self._clock(),
            cached_at=self._wall_clock(),
        )
        self._entries[key] = entry
        return entry

    def get_fresh(self, key: str) -> CachedPayload | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.age(self._clock()) <= self.ttl_seconds:
            return entry
        return None

    def get_stale(self, key: str) -> CachedPayload | None:
        """Return any entry, fresh or expired."""

        return self._entries.get(key)

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
