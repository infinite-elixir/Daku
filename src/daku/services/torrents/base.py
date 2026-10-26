"""Provider-agnostic release shape.

Every provider must return the same :class:`Release` so saving (T-008), downloading
(T-009) and the UI never care where a magnet came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class Release:
    info_hash: str
    filename: str
    quality: str
    size_bytes: int | None
    seeders: int
    provider: str
    magnet: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "info_hash": self.info_hash,
            "filename": self.filename,
            "quality": self.quality,
            "size_bytes": self.size_bytes,
            "seeders": self.seeders,
            "provider": self.provider,
            "magnet": self.magnet,
            # A snapshot dict, matching ``releases.metadata_json`` in T-008.
            "metadata_json": dict(self.metadata),
        }


class ReleaseProvider(Protocol):
    """A source of releases for one movie or episode."""

    async def search(self, media_type: str, media_id: str) -> list[Release]: ...
