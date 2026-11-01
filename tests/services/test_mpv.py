from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from daku.config import Settings
from daku.errors import AppError
from daku.services.player.mpv import MpvPlayer


class FakeProcess:
    """A child that exits when its event is set, or when it is terminated."""

    def __init__(self, *, returncode: int | None = None, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr_bytes = stderr
        self.terminated = False
        self.killed = False
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._exited = asyncio.Event()
        if returncode is not None:
            self._exited.set()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._exited.wait()
        return b"", self.stderr_bytes

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15
        self._exited.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._exited.set()


def make_player(
    settings: Settings,
    *,
    runner: Callable[..., Any],
    which: Callable[[str], str | None] = lambda _name: "/usr/bin/mpv",
    clock: Callable[[], float] | None = None,
    watch_seconds: float = 0.01,
    dedupe_seconds: float = 2.0,
) -> MpvPlayer:
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    return MpvPlayer(
        settings,
        runner=runner,
        which=which,
        watch_seconds=watch_seconds,
        dedupe_seconds=dedupe_seconds,
        ipc_connect_seconds=0.01,
        **kwargs,
    )


def test_binary_resolves_through_which(settings: Settings) -> None:
    seen: list[str] = []
    player = MpvPlayer(settings, which=lambda name: seen.append(name) or "/opt/mpv")

    assert player.binary == "/opt/mpv"
    assert seen == ["mpv"]

    missing = MpvPlayer(settings, which=lambda _name: None)
    assert missing.binary is None


async def test_launches_with_a_fixed_argv(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    process = FakeProcess()

    async def runner(*args: Any, **kwargs: Any) -> FakeProcess:
        calls.append((args, kwargs))
        return process

    player = make_player(settings, runner=runner)
    result = await player.play("media-1", Path("/media/Movies/Film.mkv"))
    await player.aclose()

    assert result.media_file_id == "media-1"
    assert result.launched is True
    args, kwargs = calls[0]
    assert args[0] == "/usr/bin/mpv"
    assert args[1] == "--no-terminal"
    assert args[2].startswith("--input-ipc-server=")
    assert args[2].endswith("mpv.sock")
    assert args[3] == "/media/Movies/Film.mkv"
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    assert "shell" not in kwargs


async def test_early_non_zero_exit_reports_the_first_stderr_line(
    settings: Settings, tmp_path: Path
) -> None:
    path = tmp_path / "Film.mkv"
    secret = str(path)
    stderr = f"[vo/gpu] Failed to open display {secret}\nsomething else\n".encode()
    process = FakeProcess(returncode=1, stderr=stderr)

    async def runner(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return process

    player = make_player(settings, runner=runner)

    with pytest.raises(AppError) as caught:
        await player.play("media-1", path)

    assert caught.value.code == "MPV_FAILED"
    assert caught.value.status_code == 502
    assert caught.value.message == "[vo/gpu] Failed to open display"
    assert secret not in caught.value.message
    assert caught.value.detail == {"exit_code": 1}


async def test_stderr_is_truncated_and_empty_stderr_uses_the_code(
    settings: Settings, tmp_path: Path
) -> None:
    long_stderr = ("x" * 400 + "\n").encode()
    failing = FakeProcess(returncode=3, stderr=long_stderr)

    async def runner(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return failing

    player = make_player(settings, runner=runner)
    with pytest.raises(AppError) as caught:
        await player.play("media-1", tmp_path / "a.mkv")
    assert len(caught.value.message) == 300

    silent = FakeProcess(returncode=4, stderr=b"   \n")

    async def silent_runner(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return silent

    player = make_player(settings, runner=silent_runner)
    with pytest.raises(AppError) as caught:
        await player.play("media-2", tmp_path / "b.mkv")
    assert caught.value.message == "mpv exited with code 4"


async def test_relaunch_terminates_only_the_tracked_child(
    settings: Settings, tmp_path: Path
) -> None:
    first = FakeProcess()
    second = FakeProcess()
    processes = [first, second]

    async def runner(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return processes.pop(0)

    player = make_player(settings, runner=runner)
    await player.play("media-1", tmp_path / "one.mkv")
    await player.play("media-2", tmp_path / "two.mkv")
    await player.aclose()

    assert first.terminated is True
    assert second.terminated is True
    assert first.killed is False


async def test_double_click_within_the_window_is_deduplicated(
    settings: Settings, tmp_path: Path
) -> None:
    calls: list[str] = []
    now = [100.0]

    async def runner(*args: Any, **_kwargs: Any) -> FakeProcess:
        calls.append(args[-1])
        return FakeProcess()

    player = make_player(settings, runner=runner, clock=lambda: now[0], dedupe_seconds=2.0)

    path = tmp_path / "one.mkv"
    first = await player.play("media-1", path)
    now[0] += 1.0
    second = await player.play("media-1", path)
    await player.aclose()

    assert first.launched and second.launched
    assert len(calls) == 1

    now[0] += 2.5
    await player.play("media-1", path)
    await player.aclose()
    assert len(calls) == 2


async def test_missing_binary_raises_unavailable(settings: Settings, tmp_path: Path) -> None:
    async def runner(*_args: Any, **_kwargs: Any) -> FakeProcess:  # pragma: no cover
        raise AssertionError("runner must not be called")

    player = make_player(settings, runner=runner, which=lambda _name: None)

    with pytest.raises(AppError) as caught:
        await player.play("media-1", tmp_path / "a.mkv")

    assert caught.value.code == "MPV_UNAVAILABLE"
    assert caught.value.status_code == 503


async def test_aclose_does_nothing_without_a_child(settings: Settings) -> None:
    player = MpvPlayer(settings)
    await player.aclose()


async def test_progress_is_read_from_the_mpv_ipc_socket(settings: Settings) -> None:
    saved: list[tuple[str, float, float | None]] = []
    ready = asyncio.Event()

    async def runner(*args: Any, **_kwargs: Any) -> FakeProcess:
        socket_path = Path(str(args[2]).split("=", 1)[1])

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # The player sends two observe_property commands on connect.
            await reader.readline()
            await reader.readline()
            ready.set()
            for message in (
                {"event": "property-change", "id": 2, "data": 600.0},
                {"event": "property-change", "id": 1, "data": 42.5},
                {"event": "property-change", "id": 1, "data": 47.5},
            ):
                writer.write((json.dumps(message) + "\n").encode())
                await writer.drain()
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            writer.close()

        server = await asyncio.start_unix_server(handler, path=str(socket_path))
        servers.append(server)
        return FakeProcess()

    servers: list[asyncio.AbstractServer] = []
    player = make_player(settings, runner=runner)
    player._sink = lambda media_id, position, duration: saved.append((media_id, position, duration))

    await player.play("media-1", Path("/media/Movies/Film.mkv"))
    await asyncio.wait_for(ready.wait(), timeout=2.0)
    for _ in range(50):
        current = player.current_progress()
        if current is not None and current["position_s"] > 0:
            break
        await asyncio.sleep(0.02)

    progress = player.current_progress()
    assert progress is not None
    assert progress["media_file_id"] == "media-1"
    assert progress["duration_s"] == 600.0
    assert progress["position_s"] in {42.5, 47.5}
    assert progress["playing"] is True

    await player.aclose()
    for server in servers:
        server.close()
        await server.wait_closed()

    assert saved, "expected at least one persisted position"
    assert saved[0][0] == "media-1"
    assert saved[0][2] == 600.0


async def test_missing_ipc_socket_never_fails_playback(settings: Settings, tmp_path: Path) -> None:
    async def runner(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return FakeProcess()

    player = make_player(settings, runner=runner)
    player._sink = lambda *_args: None

    result = await player.play("media-1", tmp_path / "Film.mkv")
    await asyncio.sleep(0.1)

    assert result.launched is True
    assert player.current_progress() is None
    await player.aclose()
