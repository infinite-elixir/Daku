"""Metadata-only magnet probing with a standalone libtorrent session.

A probe fetches the torrent *metadata* (name, piece length, file list) so the UI can
show what a magnet really contains. It must never download payload bytes, never touch
qBittorrent and never create a download row.

``libtorrent`` is an optional dependency. It is imported lazily inside
:class:`LibtorrentBackend`; importing this module works without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import logging
import re
import shutil
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from daku.config import Settings
from daku.services.releases.magnet import BTIH_PREFIX, MagnetError, ParsedMagnet, parse_magnet
from daku.services.torrents.animetosho import PUBLIC_TRACKERS

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 30.0
MAX_QUEUE = 10
CACHE_TTL_SECONDS = 24 * 60 * 60
FAILURE_TTL_SECONDS = 5 * 60
CACHE_CAPACITY = 200
POLL_INTERVAL_SECONDS = 0.1

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


class ProbeError(Exception):
    """Base class for magnet probe failures."""

    code = "PROBE_FAILED"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ProbeUnavailable(ProbeError):
    code = "PROBE_UNAVAILABLE"


class ProbeTimeout(ProbeError):
    code = "PROBE_TIMEOUT"


class ProbeBusy(ProbeError):
    code = "PROBE_BUSY"


class InvalidMagnet(ProbeError):
    code = "INVALID_MAGNET"


class ProbeFailed(ProbeError):
    code = "PROBE_FAILED"


@dataclass(slots=True)
class TorrentFile:
    index: int
    path: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "path": self.path, "size_bytes": self.size_bytes}


@dataclass(slots=True)
class TorrentMetadata:
    info_hash: str
    name: str
    total_size_bytes: int
    piece_length: int
    private: bool
    files: list[TorrentFile]
    fetched_at: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "info_hash": self.info_hash,
            "name": self.name,
            "total_size_bytes": self.total_size_bytes,
            "piece_length": self.piece_length,
            "private": self.private,
            "files": [item.to_dict() for item in self.files],
            "fetched_at": self.fetched_at,
            "cached": self.cached,
        }


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    metadata: TorrentMetadata


@dataclass(slots=True)
class _FailureEntry:
    expires_at: float
    error: ProbeError


def libtorrent_available() -> bool:
    """Whether the optional dependency can be imported, without importing it."""

    try:
        return importlib.util.find_spec("libtorrent") is not None
    except (ImportError, ValueError):
        return False


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_magnet(uri: str) -> ParsedMagnet:
    """Parse a magnet and require exactly one ``xt=urn:btih:`` parameter."""

    try:
        parts = urlsplit(uri.strip() if isinstance(uri, str) else "")
        params = parse_qs(parts.query, keep_blank_values=False)
        btih = [value for value in params.get("xt", []) if value.lower().startswith(BTIH_PREFIX)]
        if len(btih) != 1:
            raise InvalidMagnet("a magnet must contain exactly one btih info hash")
        return parse_magnet(uri)
    except MagnetError as exc:
        raise InvalidMagnet(str(exc)) from exc
    except AttributeError as exc:
        raise InvalidMagnet("magnet URI is empty") from exc


def _display_path(path: str) -> str:
    """Sanitise a torrent path for display only.

    ``/`` is a legitimate torrent path separator and is kept. The result is never
    used as a filesystem path.
    """

    cleaned = _CONTROL_RE.sub("", str(path)).strip()
    return cleaned.lstrip("/") or "unnamed"


class LibtorrentBackend:
    """Blocking libtorrent adapter. Only call this from a worker thread."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self._session: Any | None = None

    def _libtorrent(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("libtorrent")
            except ImportError as exc:  # pragma: no cover - depends on the host
                raise ProbeUnavailable(
                    "libtorrent is not installed; install the daku[probe] extra"
                ) from exc
        return self._module

    def _ensure_session(self) -> Any:
        if self._session is None:
            lt = self._libtorrent()
            self._session = lt.session(
                {
                    # DHT needs a non-loopback UDP socket to reach the swarm. Binding
                    # 127.0.0.1:0 (or "") leaves dht_nodes at 0 and every probe times out.
                    # Incoming peer connections stay disabled, so no payload can be served.
                    "listen_interfaces": "0.0.0.0:0",
                    "enable_incoming_tcp": False,
                    "enable_incoming_utp": False,
                    "enable_dht": True,
                    "enable_lsd": False,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                }
            )
        return self._session

    def probe(
        self,
        parsed: ParsedMagnet,
        *,
        save_dir: Path,
        deadline: float,
    ) -> TorrentMetadata:
        """Fetch metadata for one magnet, blocking until it arrives or the deadline."""

        lt = self._libtorrent()
        session = self._ensure_session()
        save_dir.mkdir(parents=True, exist_ok=True)
        handle: Any | None = None
        try:
            params = lt.parse_magnet_uri(parsed.magnet)
            params.save_path = str(save_dir)
            with contextlib.suppress(AttributeError, TypeError):
                # pragma: no cover - old libtorrent
                params.flags |= lt.torrent_flags.upload_mode
            handle = session.add_torrent(params)

            while True:
                status = handle.status()
                total_done = int(getattr(status, "total_done", 0) or 0)
                if total_done > 0:
                    raise ProbeFailed("payload bytes arrived during a metadata-only probe")
                if handle.has_metadata():
                    return self._metadata(handle, parsed.info_hash)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeTimeout("the magnet did not return metadata before the deadline")
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        finally:
            if handle is not None:
                try:
                    session.remove_torrent(handle)
                except Exception:  # pragma: no cover - removal must never mask the result
                    logger.warning("magnet probe torrent removal failed", exc_info=True)
            shutil.rmtree(save_dir, ignore_errors=True)

    def _metadata(self, handle: Any, info_hash: str) -> TorrentMetadata:
        info = handle.torrent_file()
        files_storage = info.files()
        files: list[TorrentFile] = []
        for index in range(int(files_storage.num_files())):
            files.append(
                TorrentFile(
                    index=index,
                    path=_display_path(files_storage.file_path(index)),
                    size_bytes=int(files_storage.file_size(index)),
                )
            )
        return TorrentMetadata(
            info_hash=info_hash.lower(),
            name=_display_path(info.name()),
            total_size_bytes=int(info.total_size()),
            piece_length=int(info.piece_length()),
            private=bool(info.priv()),
            files=files,
            fetched_at=_now_iso(),
        )

    def close(self) -> None:
        """Drop the session. libtorrent has no explicit session close call."""

        self._session = None


@dataclass(slots=True)
class ProbeStatus:
    status: str
    queue_depth: int
    last_failure_age_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "queue_depth": self.queue_depth,
            "last_failure_age_seconds": self.last_failure_age_seconds,
        }


@dataclass
class ProbeService:
    """Serialised, cached magnet probe queue.

    One probe runs at a time. At most :data:`MAX_QUEUE` requests are outstanding
    (active plus waiting); the next one is rejected with :class:`ProbeBusy`.
    """

    settings: Settings
    backend: LibtorrentBackend = field(default_factory=LibtorrentBackend)
    deadline_seconds: float = PROBE_TIMEOUT_SECONDS
    cache_ttl: float = CACHE_TTL_SECONDS
    failure_ttl: float = FAILURE_TTL_SECONDS
    cache_capacity: int = CACHE_CAPACITY
    clock: Callable[[], float] = time.monotonic

    _cache: OrderedDict[str, _CacheEntry] = field(default_factory=OrderedDict, init=False)
    _failures: dict[str, _FailureEntry] = field(default_factory=dict, init=False)
    _hash_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)
    _guard: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1), init=False)
    _outstanding: int = field(default=0, init=False)
    _last_failure_at: float | None = field(default=None, init=False)

    # ------------------------------------------------------------------ public API

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "magnet_probe", True))

    async def probe(self, magnet: str) -> TorrentMetadata:
        parsed = validate_magnet(magnet)
        key = parsed.info_hash

        cached = self._cache_get(key)
        if cached is not None:
            return cached
        failure = self._failure_get(key)
        if failure is not None:
            raise failure

        lock = self._hash_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache_get(key)
            if cached is not None:
                return cached
            failure = self._failure_get(key)
            if failure is not None:
                raise failure
            return await self._run(parsed)

    async def aclose(self) -> None:
        await asyncio.to_thread(self.backend.close)

    def status(self) -> ProbeStatus:
        session = getattr(self.backend, "_session", None)
        available = self.enabled and (session is not None or libtorrent_available())
        if not self.enabled:
            status = "disabled"
        elif available:
            status = "ok"
        else:
            status = "unavailable"
        age = None
        if self._last_failure_at is not None:
            age = max(0.0, round(self.clock() - self._last_failure_at, 3))
        return ProbeStatus(
            status=status,
            queue_depth=self._outstanding,
            last_failure_age_seconds=age,
        )

    # ------------------------------------------------------------------ internals

    async def _run(self, parsed: ParsedMagnet) -> TorrentMetadata:
        key = parsed.info_hash
        await self._enter_queue()
        try:
            save_dir = self.settings.data_dir / "probes" / key
            probe_magnet = parsed.magnet
            if not parsed.trackers:
                additions = "".join(f"&tr={quote(tracker, safe='')}" for tracker in PUBLIC_TRACKERS)
                probe_magnet = f"{parsed.magnet}{additions}"

            logger.info(
                "magnet probe started",
                extra={"info_hash": key[:12], "deadline_s": self.deadline_seconds},
            )
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self.backend.probe,
                    _with_magnet(parsed, probe_magnet),
                    save_dir=save_dir,
                    deadline=time.monotonic() + self.deadline_seconds,
                )
            )
            try:
                metadata = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Do not release the single-probe slot while the worker thread runs.
                with contextlib.suppress(Exception):
                    await worker
                raise
        except ProbeError as exc:
            self._record_failure(key, exc)
            logger.warning(
                "magnet probe failed",
                extra={"info_hash": key[:12], "code": exc.code},
            )
            raise
        except Exception as exc:  # pragma: no cover - unexpected adapter failure
            logger.warning("magnet probe failed", extra={"info_hash": key[:12]}, exc_info=True)
            self._record_failure(key, ProbeFailed("the magnet probe failed unexpectedly"))
            raise ProbeFailed("the magnet probe failed unexpectedly") from exc
        else:
            metadata.info_hash = key
            self._cache_put(key, metadata)
            logger.info(
                "magnet probe finished",
                extra={"info_hash": key[:12], "files": len(metadata.files)},
            )
            return metadata
        finally:
            await self._leave_queue()
            shutil.rmtree(save_dir, ignore_errors=True)

    async def _enter_queue(self) -> None:
        async with self._guard:
            if self._outstanding >= MAX_QUEUE:
                raise ProbeBusy("the magnet probe queue is full; try again in a moment")
            self._outstanding += 1
        try:
            await self._semaphore.acquire()
        except BaseException:
            async with self._guard:
                self._outstanding = max(0, self._outstanding - 1)
            raise

    async def _leave_queue(self) -> None:
        async with self._guard:
            self._outstanding = max(0, self._outstanding - 1)
        self._semaphore.release()

    def _cache_get(self, key: str) -> TorrentMetadata | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self.clock():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return TorrentMetadata(
            info_hash=entry.metadata.info_hash,
            name=entry.metadata.name,
            total_size_bytes=entry.metadata.total_size_bytes,
            piece_length=entry.metadata.piece_length,
            private=entry.metadata.private,
            files=list(entry.metadata.files),
            fetched_at=entry.metadata.fetched_at,
            cached=True,
        )

    def _cache_put(self, key: str, metadata: TorrentMetadata) -> None:
        self._failures.pop(key, None)
        self._cache[key] = _CacheEntry(
            expires_at=self.clock() + self.cache_ttl,
            metadata=metadata,
        )
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_capacity:
            self._cache.popitem(last=False)

    def _failure_get(self, key: str) -> ProbeError | None:
        entry = self._failures.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self.clock():
            self._failures.pop(key, None)
            return None
        return entry.error

    def _record_failure(self, key: str, error: ProbeError) -> None:
        self._last_failure_at = self.clock()
        if isinstance(error, (ProbeUnavailable, InvalidMagnet)):
            return
        self._failures[key] = _FailureEntry(
            expires_at=self.clock() + self.failure_ttl,
            error=error,
        )


def _with_magnet(parsed: ParsedMagnet, magnet: str) -> ParsedMagnet:
    return ParsedMagnet(
        info_hash=parsed.info_hash,
        filename=parsed.filename,
        trackers=parsed.trackers,
        magnet=magnet,
        quality=parsed.quality,
        metadata=parsed.metadata,
        provider=parsed.provider,
        size_bytes=parsed.size_bytes,
        seeders=parsed.seeders,
    )
