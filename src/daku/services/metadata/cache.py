"""Small in-process TTL cache for metadata upstreams."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CacheEntry:
    value: Any
    stored_at: float

    def age(self, now: float) -> float:
        return now - self.stored_at


class TTLCache:
    """Cache JSON payloads by full URL.

    Entries stay readable after they expire so callers can serve stale data when an
    upstream is down. Callers decide whether stale data is acceptable.
    """

    def __init__(self, ttl_seconds: float = 3600.0, *, clock: Any = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, CacheEntry] = {}

    def set(self, key: str, value: Any, *, stored_at: float | None = None) -> None:
        self._entries[key] = CacheEntry(
            value=value,
            stored_at=self._clock() if stored_at is None else stored_at,
        )

    def get(self, key: str) -> CacheEntry | None:
        return self._entries.get(key)

    def get_fresh(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.age(self._clock()) <= self.ttl_seconds:
            return entry
        return None

    def get_stale(self, key: str) -> CacheEntry | None:
        """Return any entry, fresh or expired."""

        return self._entries.get(key)

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
