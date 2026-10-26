"""The contract every download engine implements.

qBittorrent owns torrents, aria2 owns direct HTTP downloads. The worker only ever
talks to this protocol, so the state machine is identical for both engines.

All engine methods are async and never raise for a normal remote failure: they raise
:class:`EngineError` with a stable code so the worker can decide between "keep the row
alive and retry" (unreachable) and "the engine rejected this" (failed).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class EngineError(Exception):
    """A download engine could not complete an operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def transient(self) -> bool:
        """Transient errors leave the download row in place for a retry."""

        return self.code in TRANSIENT_CODES


TRANSIENT_CODES: frozenset[str] = frozenset(
    {
        "QBT_UNREACHABLE",
        "QBT_LOGIN_FAILED",
        "ARIA2_UNREACHABLE",
        "ARIA2_UNAVAILABLE",
    }
)


@dataclass(slots=True)
class AddRequest:
    """Everything an engine needs to start one download."""

    external_id: str
    url: str
    save_path: str
    filename: str | None = None
    tag: str | None = None


@dataclass(slots=True)
class EngineStatus:
    """One normalised engine snapshot."""

    external_id: str
    status: str
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed_bps: int | None = None
    eta_seconds: int | None = None
    error: str | None = None
    files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Health:
    """Engine reachability plus a human-readable detail line."""

    ok: bool
    detail: str
    version: str | None = None


class DownloadEngine(Protocol):
    """A download engine Daku can drive."""

    name: str

    async def add(self, request: AddRequest) -> str:
        """Start a download and return its engine-side identifier."""
        ...

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        """Return snapshots keyed by lowercased external id."""
        ...

    async def pause(self, external_id: str) -> None: ...

    async def resume(self, external_id: str) -> None: ...

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None: ...

    async def health(self) -> Health: ...
