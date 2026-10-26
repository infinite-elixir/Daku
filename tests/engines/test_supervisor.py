from __future__ import annotations

from typing import Any

import pytest

from daku.config import Settings
from daku.services.engines.aria2 import Aria2Engine
from daku.services.engines.base import Health
from daku.services.engines.supervisor import Aria2Supervisor


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _health(ok: bool) -> Health:
    return Health(ok=ok, detail="aria2 1.36" if ok else "unreachable")


def _supervisor(
    settings: Settings,
    *,
    probe: Any,
    spawn: Any = None,
    clock: Any = None,
) -> Aria2Supervisor:
    return Aria2Supervisor(
        settings,
        Aria2Engine(settings.model_copy(update={"aria2_rpc": "http://127.0.0.1:6800/jsonrpc"})),
        binary="/usr/bin/aria2c",
        probe=probe,
        spawn=spawn,
        clock=clock or FakeClock(),
        startup_timeout=0,
    )


async def test_adopts_an_already_running_aria2(settings: Settings) -> None:
    spawned: list[list[str]] = []

    async def spawn(args: list[str]) -> bool:
        spawned.append(args)
        return True

    async def probe() -> Health:
        return _health(True)

    supervisor = _supervisor(settings, probe=probe, spawn=spawn)

    assert await supervisor.ensure_started() is True
    assert spawned == []
    assert supervisor.running is False


async def test_spawns_with_the_required_flags(settings: Settings) -> None:
    spawned: list[list[str]] = []

    async def spawn(args: list[str]) -> bool:
        spawned.append(args)
        return True

    async def probe() -> Health:
        return _health(False)

    supervisor = _supervisor(settings, probe=probe, spawn=spawn)

    assert await supervisor.ensure_started() is True
    args = spawned[0]
    joined = " ".join(args)
    assert "--enable-rpc=true" in args
    assert "--rpc-listen-port=6800" in args
    assert "--rpc-listen-all=false" in args
    assert "--continue=true" in args
    assert "--max-concurrent-downloads=3" in args
    assert "save-session" in joined
    assert supervisor.session_file.name == ".daku-aria2.session"
    assert supervisor.session_file.parent == settings.downloads_dir
    # URLs are never passed on the command line.
    assert "http://" not in joined.replace("--rpc-listen", "")
    assert "magnet:" not in joined


async def test_backoff_doubles_and_caps_at_eight_seconds(settings: Settings) -> None:
    clock = FakeClock()
    attempts: list[float] = []

    async def spawn(args: list[str]) -> bool:
        attempts.append(clock.now)
        return False

    async def probe() -> Health:
        return _health(False)

    supervisor = _supervisor(settings, probe=probe, spawn=spawn, clock=clock)

    assert await supervisor.ensure_started() is False
    assert supervisor.backoff_seconds == 2.0
    # Backoff is active: a tick immediately after the failure must not respawn.
    assert await supervisor.ensure_started() is False
    assert len(attempts) == 1

    clock.advance(1.0)
    assert await supervisor.ensure_started() is False
    assert len(attempts) == 2
    assert supervisor.backoff_seconds == 4.0

    clock.advance(2.0)
    assert await supervisor.ensure_started() is False
    assert supervisor.backoff_seconds == 8.0

    clock.advance(4.0)
    assert await supervisor.ensure_started() is False
    assert supervisor.backoff_seconds == 8.0


async def test_missing_binary_disables_starting(settings: Settings, monkeypatch: Any) -> None:
    monkeypatch.setattr("daku.services.engines.supervisor.shutil.which", lambda _name: None)

    async def probe() -> Health:
        return _health(False)

    supervisor = Aria2Supervisor(settings, Aria2Engine(settings), binary=None, probe=probe)

    assert supervisor.available is False
    assert await supervisor.ensure_started() is False


async def test_stop_terminates_only_an_owned_process(settings: Settings) -> None:
    async def probe() -> Health:
        return _health(True)

    supervisor = _supervisor(settings, probe=probe)
    proc = FakeProc()
    supervisor._proc = proc
    supervisor._owns_process = True

    await supervisor.stop()

    assert proc.terminated is True
    assert supervisor.running is False

    adopted = FakeProc()
    supervisor._proc = adopted
    supervisor._owns_process = False
    await supervisor.stop()
    assert adopted.terminated is False


@pytest.mark.parametrize(
    ("rpc", "expected"),
    [
        ("http://127.0.0.1:6800/jsonrpc", ["--rpc-listen-all=false", "--rpc-listen-port=6800"]),
        ("http://0.0.0.0:7000/jsonrpc", ["--rpc-listen-all=true", "--rpc-listen-port=7000"]),
    ],
)
def test_listen_flags_follow_the_rpc_url(settings: Settings, rpc: str, expected: list[str]) -> None:
    configured = settings.model_copy(update={"aria2_rpc": rpc, "aria2_bin": "/usr/bin/aria2c"})
    supervisor = Aria2Supervisor(configured, Aria2Engine(configured))

    args = supervisor.arguments()

    for flag in expected:
        assert flag in args
