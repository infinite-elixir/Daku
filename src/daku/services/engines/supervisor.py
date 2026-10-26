"""aria2c process supervision.

Daku adopts an aria2c that is already listening on the configured RPC port and only
spawns its own when the port is dead. The session file lives inside ``downloads_dir``
so a respawn can resume partial files. A crash is retried with capped backoff; the
worker calls :meth:`Aria2Supervisor.ensure_started` on every tick that has aria2 rows,
so a killed process is back within one poll interval (2 s) and never later than 10 s.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from daku.config import Settings
from daku.services.engines.aria2 import Aria2Engine
from daku.services.engines.base import Health

MAX_BACKOFF_SECONDS = 8.0
STARTUP_TIMEOUT_SECONDS = 5.0
STARTUP_POLL_SECONDS = 0.1
SESSION_FILE_NAME = ".daku-aria2.session"

ProbeFn = Callable[[], Awaitable[Health]]
SpawnFn = Callable[[list[str]], Awaitable[Any]]


def rpc_endpoint(settings: Settings) -> tuple[str, int]:
    """Return the ``(host, port)`` aria2c must listen on."""

    parts = urlsplit(settings.aria2_rpc)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 6800
    return host, port


class Aria2Supervisor:
    """Start, adopt and stop the aria2c process behind :class:`Aria2Engine`."""

    def __init__(
        self,
        settings: Settings,
        engine: Aria2Engine,
        *,
        binary: str | None = None,
        probe: ProbeFn | None = None,
        spawn: SpawnFn | None = None,
        clock: Callable[[], float] = time.monotonic,
        startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        resolved_binary = binary if binary is not None else settings.aria2_bin
        self._binary = resolved_binary or shutil.which("aria2c")
        self._settings = settings
        self._engine = engine
        self._probe = probe or engine.health
        self._spawn = spawn or self._spawn_process
        self._clock = clock
        self._startup_timeout = startup_timeout
        self._proc: Any | None = None
        self._backoff = 1.0
        self._next_attempt = 0.0
        self._owns_process = False
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self._binary)

    @property
    def binary(self) -> str | None:
        return self._binary

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and getattr(proc, "returncode", None) is None

    @property
    def backoff_seconds(self) -> float:
        return self._backoff

    @property
    def session_file(self) -> Path:
        return self._settings.downloads_dir / SESSION_FILE_NAME

    def arguments(self) -> list[str]:
        host, port = rpc_endpoint(self._settings)
        listen_all = "false" if host in {"127.0.0.1", "localhost", "::1"} else "true"
        args = [
            self._binary or "aria2c",
            "--enable-rpc=true",
            f"--rpc-listen-all={listen_all}",
            f"--rpc-listen-port={port}",
            f"--dir={self._settings.downloads_dir}",
            "--continue=true",
            "--max-concurrent-downloads=3",
            f"--save-session={self.session_file}",
            "--save-session-interval=30",
            # Never pass a URL on the command line: only RPC starts downloads.
        ]
        if self._settings.aria2_secret:
            args.append(f"--rpc-secret={self._settings.aria2_secret}")
        if self.session_file.is_file() and self.session_file.stat().st_size > 0:
            args.append(f"--input-file={self.session_file}")
        return args

    async def ensure_started(self) -> bool:
        """Adopt a live aria2 or spawn one, respecting restart backoff."""

        health = await self._probe()
        if health.ok:
            return True
        if not self.available:
            return False
        now = self._clock()
        if now < self._next_attempt:
            return False

        async with self._lock:
            # Another tick may have spawned it while we waited for the lock.
            if (await self._probe()).ok:
                return True
            ok = await self._spawn(self.arguments())
            if ok:
                self._backoff = 1.0
                self._next_attempt = 0.0
                return True
            self._next_attempt = self._clock() + self._backoff
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SECONDS)
            return False

    async def _spawn_process(self, args: list[str]) -> bool:
        self._settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        session_dir = self.session_file.parent
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, ValueError):
            self._proc = None
            return False
        self._owns_process = True

        deadline = self._clock() + self._startup_timeout
        while self._clock() < deadline:
            if (await self._probe()).ok:
                return True
            await asyncio.sleep(STARTUP_POLL_SECONDS)
        return False

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or not self._owns_process:
            return
        self._owns_process = False
        if getattr(proc, "returncode", None) is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
