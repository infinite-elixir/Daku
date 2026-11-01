"""Launch the host's own mpv for a media file Daku filed.

The player lives behind this module so a socket-based player can replace it later
without touching the router. Nothing from the request reaches argv: the caller passes
only a resolved library path.

Progress is read from mpv's JSON IPC socket (``--input-ipc-server``), which mpv creates
for us. The socket is only ever read, never used to send playback commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daku.config import Settings
from daku.errors import AppError
from daku.utils import utc_now_iso

logger = logging.getLogger(__name__)

MPV_UNAVAILABLE = "MPV_UNAVAILABLE"
MPV_FAILED = "MPV_FAILED"

WATCH_SECONDS = 2.0
DEDUPE_SECONDS = 2.0
TERMINATE_GRACE_SECONDS = 2.0
STDERR_LIMIT = 300

# Persist at most every five seconds of playback; the live value updates on every event.
PROGRESS_SAVE_SECONDS = 5.0
IPC_CONNECT_SECONDS = 3.0
IPC_CONNECT_INTERVAL = 0.05
IPC_STOP_SECONDS = 2.0
SOCKET_NAME = "mpv.sock"

Runner = Callable[..., Awaitable[Any]]
Which = Callable[[str], str | None]
ProgressSink = Callable[[str, float, float | None], None]


@dataclass(frozen=True, slots=True)
class LaunchResult:
    media_file_id: str
    launched: bool


@dataclass(frozen=True, slots=True)
class PlaybackProgress:
    media_file_id: str
    position_s: float
    duration_s: float | None
    playing: bool
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_file_id": self.media_file_id,
            "position_s": self.position_s,
            "duration_s": self.duration_s,
            "playing": self.playing,
            "updated_at": self.updated_at,
        }


def _unavailable() -> AppError:
    return AppError(
        code=MPV_UNAVAILABLE,
        message="mpv is not installed or is not on PATH",
        status_code=503,
    )


def _first_line(stderr: bytes, path: Path, return_code: int) -> str:
    text = stderr.decode("utf-8", errors="replace").replace(str(path), "").strip()
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if not line:
        return f"mpv exited with code {return_code}"
    return line[:STDERR_LIMIT]


class MpvPlayer:
    """Owns at most one tracked child process; never touches processes it did not start."""

    def __init__(
        self,
        settings: Settings,
        *,
        which: Which | None = None,
        runner: Runner | None = None,
        progress_sink: ProgressSink | None = None,
        ipc_dir: Path | None = None,
        ipc_connect_seconds: float = IPC_CONNECT_SECONDS,
        watch_seconds: float = WATCH_SECONDS,
        dedupe_seconds: float = DEDUPE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._which = which or shutil.which
        self._runner = runner or asyncio.create_subprocess_exec
        self._sink = progress_sink
        self._ipc_dir = ipc_dir or (settings.data_dir / "mpv")
        self.ipc_connect_seconds = ipc_connect_seconds
        self.watch_seconds = watch_seconds
        self.dedupe_seconds = dedupe_seconds
        self._clock = clock
        self._process: Any = None
        self._drain: asyncio.Task[Any] | None = None
        self._tracker: asyncio.Task[Any] | None = None
        self._live: PlaybackProgress | None = None
        self._last_id: str | None = None
        self._last_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def binary(self) -> str | None:
        return self._which(self.settings.mpv_bin)

    def current_progress(self) -> dict[str, Any] | None:
        """The tracked child's last known position, or ``None`` when idle."""

        live = self._live
        return live.to_dict() if live is not None else None

    async def aclose(self) -> None:
        await self._terminate_current()

    async def play(self, media_file_id: str, path: Path) -> LaunchResult:
        entered = self._clock()
        async with self._lock:
            if (
                self._last_id == media_file_id
                and self._last_at is not None
                and entered - self._last_at < self.dedupe_seconds
            ):
                return LaunchResult(media_file_id=media_file_id, launched=True)

            await self._terminate_current()

            binary = self.binary
            if binary is None:
                raise _unavailable()

            socket_path = self._reset_socket()
            process = await self._runner(
                binary,
                "--no-terminal",
                f"--input-ipc-server={socket_path}",
                str(path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self._process = process
            self._live = PlaybackProgress(
                media_file_id=media_file_id,
                position_s=0.0,
                duration_s=None,
                playing=True,
                updated_at=utc_now_iso(),
            )
            comms: asyncio.Task[tuple[bytes, bytes]] = asyncio.create_task(process.communicate())
            comms.add_done_callback(lambda _task: self._forget(process))
            self._drain = comms
            self._tracker = asyncio.create_task(
                self._track(media_file_id, socket_path, process),
                name="mpv-progress",
            )

            done, _pending = await asyncio.wait({comms}, timeout=self.watch_seconds)
            if not done:
                # Still running after the window: that is a successful launch. The
                # communicate task stays alive to drain the pipes and clear the pid.
                self._last_id = media_file_id
                self._last_at = self._clock()
                logger.info("mpv launched", extra={"media_file_id": media_file_id})
                return LaunchResult(media_file_id=media_file_id, launched=True)

            _stdout, stderr = comms.result()
            return_code = process.returncode if process.returncode is not None else -1
            if return_code != 0:
                message = _first_line(stderr, path, return_code)
                logger.warning(
                    "mpv exited early",
                    extra={"media_file_id": media_file_id, "exit_code": return_code},
                )
                raise AppError(
                    code=MPV_FAILED,
                    message=message,
                    status_code=502,
                    detail={"exit_code": return_code},
                )

            self._last_id = media_file_id
            self._last_at = self._clock()
            logger.info("mpv launched", extra={"media_file_id": media_file_id})
            return LaunchResult(media_file_id=media_file_id, launched=True)

    def _reset_socket(self) -> Path:
        self._ipc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        socket_path = self._ipc_dir / SOCKET_NAME
        with suppress(FileNotFoundError, OSError):
            socket_path.unlink()
        return socket_path

    async def _track(self, media_file_id: str, socket_path: Path, process: Any) -> None:
        """Read ``time-pos`` / ``duration`` from mpv until it exits. Never raises."""

        position = 0.0
        duration: float | None = None
        saved_at = -PROGRESS_SAVE_SECONDS
        writer: asyncio.StreamWriter | None = None
        connected = False
        seen = False
        try:
            connection = await self._connect(socket_path, process)
            if connection is None:
                return
            connected = True
            reader, writer = connection
            writer.write(
                b'{"command": ["observe_property", 1, "time-pos"]}\n'
                b'{"command": ["observe_property", 2, "duration"]}\n'
            )
            await writer.drain()

            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(message, dict) or message.get("event") != "property-change":
                    continue
                value = message.get("data")
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                if message.get("id") == 1:
                    position = max(0.0, float(value))
                elif message.get("id") == 2 and float(value) > 0:
                    duration = float(value)
                else:
                    continue
                seen = True
                self._live = PlaybackProgress(
                    media_file_id=media_file_id,
                    position_s=position,
                    duration_s=duration,
                    playing=True,
                    updated_at=utc_now_iso(),
                )
                if position - saved_at >= PROGRESS_SAVE_SECONDS:
                    saved_at = position
                    await self._emit(media_file_id, position, duration, playing=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - progress must never affect playback
            logger.debug("mpv progress tracking stopped", exc_info=True)
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
            # The child exited or the tracking task was cancelled: record the last position.
            if connected and (seen or position > 0):
                with suppress(Exception):
                    await self._emit(media_file_id, position, duration, playing=False)
            elif self._live is not None and self._live.media_file_id == media_file_id:
                self._live = None
            if self._tracker is asyncio.current_task():
                self._tracker = None

    async def _connect(
        self, socket_path: Path, process: Any
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        deadline = time.monotonic() + self.ipc_connect_seconds
        while time.monotonic() < deadline:
            if getattr(process, "returncode", None) is not None:
                return None
            try:
                return await asyncio.open_unix_connection(str(socket_path))
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                await asyncio.sleep(IPC_CONNECT_INTERVAL)
        return None

    async def _emit(
        self,
        media_file_id: str,
        position_s: float,
        duration_s: float | None,
        *,
        playing: bool,
    ) -> None:
        self._live = PlaybackProgress(
            media_file_id=media_file_id,
            position_s=position_s,
            duration_s=duration_s,
            playing=playing,
            updated_at=utc_now_iso(),
        )
        sink = self._sink
        if sink is None:
            return
        try:
            await asyncio.to_thread(sink, media_file_id, position_s, duration_s)
        except Exception:  # pragma: no cover - a sink failure must not stop playback
            logger.warning(
                "could not persist playback progress", extra={"media_file_id": media_file_id}
            )

    def _forget(self, process: Any) -> None:
        if self._process is process:
            self._process = None
            self._drain = None

    async def _terminate_current(self) -> None:
        process = self._process
        if process is None:
            return
        if getattr(process, "returncode", None) is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
        drain = self._drain
        if drain is not None and not drain.done():
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - a reaper must never fail a launch
                pass
        tracker = self._tracker
        if tracker is not None and not tracker.done():
            with suppress(TimeoutError):
                await asyncio.wait_for(tracker, timeout=IPC_STOP_SECONDS)
            if not tracker.done():
                tracker.cancel()
                with suppress(asyncio.CancelledError):
                    await tracker
        self._process = None
        self._drain = None
        self._tracker = None
        with suppress(FileNotFoundError, OSError):
            (self._ipc_dir / SOCKET_NAME).unlink()
