from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from daku.config import Settings
from daku.services.torrents.probe import (
    InvalidMagnet,
    LibtorrentBackend,
    ProbeBusy,
    ProbeFailed,
    ProbeService,
    ProbeTimeout,
)


class FakeFileStorage:
    def __init__(self, files: list[tuple[str, int]]) -> None:
        self._files = files

    def num_files(self) -> int:
        return len(self._files)

    def file_path(self, index: int) -> str:
        return self._files[index][0]

    def file_size(self, index: int) -> int:
        return self._files[index][1]


class FakeInfo:
    def __init__(
        self,
        *,
        name: str = "Example.S01.1080p",
        files: list[tuple[str, int]] | None = None,
        piece_length: int = 262144,
        private: bool = False,
    ) -> None:
        self._name = name
        self._files = FakeFileStorage(
            files
            if files is not None
            else [
                ("Example.S01E01.mkv", 1_000),
                ("Example.S01E02.mkv", 2_000),
            ]
        )
        self._piece_length = piece_length
        self._private = private

    def name(self) -> str:
        return self._name

    def files(self) -> FakeFileStorage:
        return self._files

    def total_size(self) -> int:
        return sum(size for _path, size in self._files._files)

    def piece_length(self) -> int:
        return self._piece_length

    def priv(self) -> bool:
        return self._private


class FakeStatus:
    def __init__(self, total_done: int = 0) -> None:
        self.total_done = total_done


class FakeHandle:
    def __init__(
        self,
        info: FakeInfo,
        *,
        metadata_after: int = 0,
        payload_bytes: int = 0,
        raise_after: int | None = None,
    ) -> None:
        self._info = info
        self._metadata_after = metadata_after
        self._payload_bytes = payload_bytes
        self._raise_after = raise_after
        self._polls = 0

    def status(self) -> FakeStatus:
        return FakeStatus(self._payload_bytes)

    def has_metadata(self) -> bool:
        self._polls += 1
        if self._raise_after is not None and self._polls >= self._raise_after:
            raise RuntimeError("simulated libtorrent error")
        return self._polls > self._metadata_after

    def torrent_file(self) -> FakeInfo:
        return self._info


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.removed: list[Any] = []

    def add_torrent(self, params: Any) -> FakeHandle:
        self.added.append(params)
        return params.handle

    def remove_torrent(self, handle: Any) -> None:
        self.removed.append(handle)


class FakeLibtorrent:
    torrent_flags = SimpleNamespace(upload_mode=1)

    def __init__(self, handle: FakeHandle) -> None:
        self.handle = handle
        self.sessions: list[FakeSession] = []
        self.settings: list[dict[str, Any]] = []
        self.parsed: list[str] = []

    def session(self, settings: dict[str, Any]) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        self.settings.append(settings)
        return session

    def parse_magnet_uri(self, magnet: str) -> Any:
        self.parsed.append(magnet)
        return SimpleNamespace(
            save_path=None,
            flags=0,
            handle=self.handle,
        )


def magnetic(info_hash: str) -> str:
    return f"magnet:?xt=urn:btih:{info_hash}"


def make_service(
    settings: Settings,
    lt: FakeLibtorrent,
    *,
    clock: Any = None,
    capacity: int = 200,
    deadline: float = 1.0,
) -> ProbeService:
    return ProbeService(
        settings,
        backend=LibtorrentBackend(module=lt),
        deadline_seconds=deadline,
        cache_capacity=capacity,
        clock=clock or (lambda: 1_000.0),
    )


async def test_probe_returns_the_file_list_and_cleans_up(settings: Settings) -> None:
    info = FakeInfo(
        name="Season Pack",
        files=[
            ("Season 01/Episode 01.mkv", 1_500),
            ("Season 01/Episode 02.mkv", 2_500),
        ],
        piece_length=524288,
        private=True,
    )
    lt = FakeLibtorrent(FakeHandle(info, metadata_after=1))
    service = make_service(settings, lt)

    metadata = await service.probe(magnetic("a" * 40))

    assert metadata.info_hash == "a" * 40
    assert metadata.name == "Season Pack"
    assert metadata.total_size_bytes == 4_000
    assert metadata.piece_length == 524288
    assert metadata.private is True
    assert [item.path for item in metadata.files] == [
        "Season 01/Episode 01.mkv",
        "Season 01/Episode 02.mkv",
    ]
    assert metadata.cached is False
    session = lt.sessions[0]
    assert len(session.added) == 1
    assert len(session.removed) == 1
    assert not (settings.data_dir / "probes" / ("a" * 40)).exists()
    # DHT must bind a non-loopback socket; incoming peer connections stay disabled so a
    # probe can only fetch metadata, never serve or receive payload bytes.
    assert lt.settings[0]["listen_interfaces"] == "0.0.0.0:0"
    assert lt.settings[0]["enable_incoming_tcp"] is False
    assert lt.settings[0]["enable_incoming_utp"] is False
    assert lt.settings[0]["enable_dht"] is True
    assert lt.settings[0]["enable_lsd"] is False
    assert lt.settings[0]["enable_upnp"] is False
    assert lt.settings[0]["enable_natpmp"] is False


async def test_single_file_torrent_has_one_row(settings: Settings) -> None:
    info = FakeInfo(name="Movie.mkv", files=[("Movie.mkv", 8_000)])
    service = make_service(settings, FakeLibtorrent(FakeHandle(info)))

    metadata = await service.probe(magnetic("b" * 40))

    assert len(metadata.files) == 1
    assert metadata.files[0].index == 0
    assert metadata.total_size_bytes == 8_000


async def test_missing_trackers_get_the_public_default(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt)

    await service.probe(magnetic("c" * 40))

    assert "&tr=" in lt.parsed[0]
    assert "udp%3A%2F%2Ftracker.opentrackr.org" in lt.parsed[0]


async def test_payload_bytes_abort_the_probe(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo(), metadata_after=50, payload_bytes=1))
    service = make_service(settings, lt, deadline=0.2)

    with pytest.raises(ProbeFailed):
        await service.probe(magnetic("d" * 40))

    assert len(lt.sessions[0].removed) == 1
    assert not (settings.data_dir / "probes" / ("d" * 40)).exists()


async def test_timeout_raises_and_removes_the_torrent(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo(), metadata_after=10_000))
    service = make_service(settings, lt, deadline=0.05)

    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("e" * 40))

    assert len(lt.sessions[0].removed) == 1
    assert not (settings.data_dir / "probes" / ("e" * 40)).exists()


async def test_adapter_error_removes_the_torrent_and_is_wrapped(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo(), raise_after=1))
    service = make_service(settings, lt)

    with pytest.raises(ProbeFailed):
        await service.probe(magnetic("f" * 40))

    assert len(lt.sessions[0].removed) == 1


async def test_success_is_cached_without_a_second_libtorrent_call(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt)

    first = await service.probe(magnetic("1" * 40))
    second = await service.probe(magnetic("1" * 40))

    assert first.cached is False
    assert second.cached is True
    assert len(lt.sessions[0].added) == 1


async def test_cache_expires_after_24_hours(settings: Settings) -> None:
    now = [1_000.0]
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt, clock=lambda: now[0])

    await service.probe(magnetic("2" * 40))
    now[0] += 24 * 60 * 60 + 1
    metadata = await service.probe(magnetic("2" * 40))

    assert metadata.cached is False
    assert len(lt.sessions[0].added) == 2


async def test_cache_is_lru_with_a_capacity(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt, capacity=2)

    await service.probe(magnetic("3" * 40))
    await service.probe(magnetic("4" * 40))
    # Touch the oldest entry so the second hash becomes the LRU one.
    await service.probe(magnetic("3" * 40))
    await service.probe(magnetic("5" * 40))
    assert len(lt.sessions[0].added) == 3

    # 3 was recently used and is still cached; 4 was evicted.
    cached = await service.probe(magnetic("3" * 40))
    assert cached.cached is True
    assert len(lt.sessions[0].added) == 3

    evicted = await service.probe(magnetic("4" * 40))
    assert evicted.cached is False
    assert len(lt.sessions[0].added) == 4


async def test_failure_is_cached_for_five_minutes(settings: Settings) -> None:
    now = [1_000.0]
    lt = FakeLibtorrent(FakeHandle(FakeInfo(), metadata_after=10_000))
    service = make_service(settings, lt, clock=lambda: now[0], deadline=0.02)

    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("6" * 40))
    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("6" * 40))
    assert sum(len(session.added) for session in lt.sessions) == 1

    now[0] += 5 * 60 + 1
    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("6" * 40))
    assert sum(len(session.added) for session in lt.sessions) == 2


async def test_invalid_magnets_never_reach_libtorrent(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt)

    for bad in (
        "not-a-magnet",
        "magnet:?dn=NoHash",
        f"magnet:?xt=urn:btih:{'z' * 40}",
        f"magnet:?xt=urn:btih:{'a' * 40}&xt=urn:btih:{'b' * 40}",
    ):
        with pytest.raises(InvalidMagnet):
            await service.probe(bad)

    assert lt.sessions == []
    assert lt.parsed == []


def test_display_paths_strip_control_characters_only_when_displaying() -> None:
    from daku.services.torrents.probe import _display_path

    assert _display_path("\x00evil\x01/Season 01/file.mkv") == "evil/Season 01/file.mkv"
    assert _display_path("/absolute/path.mkv") == "absolute/path.mkv"


class BlockingBackend:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    def probe(self, parsed: Any, *, save_dir: Any, deadline: float) -> Any:
        from daku.services.torrents.probe import TorrentFile, TorrentMetadata

        self.calls += 1
        self.started.set()
        self.release.wait(5)
        return TorrentMetadata(
            info_hash=parsed.info_hash,
            name="Blocked",
            total_size_bytes=1,
            piece_length=1,
            private=False,
            files=[TorrentFile(index=0, path="file.mkv", size_bytes=1)],
            fetched_at="2026-10-26T00:00:00Z",
        )

    def close(self) -> None:
        return None


async def test_the_eleventh_concurrent_probe_is_rejected(settings: Settings) -> None:
    backend = BlockingBackend()
    service = ProbeService(settings, backend=backend)  # type: ignore[arg-type]
    tasks = [asyncio.create_task(service.probe(magnetic(f"{index:040x}"))) for index in range(10)]
    await asyncio.sleep(0.2)
    assert backend.started.is_set()

    with pytest.raises(ProbeBusy):
        await service.probe(magnetic("f" * 40))

    backend.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert not [item for item in results if isinstance(item, BaseException)]
    assert backend.calls == 10


async def test_duplicate_requests_share_one_probe(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    service = make_service(settings, lt)

    first, second = await asyncio.gather(
        service.probe(magnetic("7" * 40)),
        service.probe(magnetic("7" * 40)),
    )

    assert {first.cached, second.cached} == {False, True}
    assert len(lt.sessions[0].added) == 1


def _clocks() -> tuple[Any, Any]:
    wall = [1_000.0]
    mono = [1_000.0]
    return wall, mono


def _backend_with_clocks(lt: FakeLibtorrent) -> tuple[LibtorrentBackend, Any, Any]:
    wall, mono = _clocks()
    backend = LibtorrentBackend(
        module=lt,
        clock=lambda: mono[0],
        wall_clock=lambda: wall[0],
    )
    return backend, wall, mono


async def test_a_warm_session_is_reused_between_probes(settings: Settings) -> None:
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    backend, wall, mono = _backend_with_clocks(lt)
    service = ProbeService(settings, backend=backend, deadline_seconds=1.0)

    await service.probe(magnetic("8" * 40))
    wall[0] += 5
    mono[0] += 5
    await service.probe(magnetic("9" * 40))

    assert len(lt.sessions) == 1


async def test_a_long_system_resume_rebuilds_the_session(settings: Settings) -> None:
    # libtorrent sessions do not survive a suspend: after a 39 minute sleep the cached
    # session times every probe out until the process restarts. A wall-clock jump much
    # larger than the monotonic delta means the host slept, so the session is rebuilt.
    lt = FakeLibtorrent(FakeHandle(FakeInfo()))
    backend, wall, mono = _backend_with_clocks(lt)
    service = ProbeService(settings, backend=backend, deadline_seconds=1.0)

    await service.probe(magnetic("8" * 40))
    assert len(lt.sessions) == 1

    wall[0] += 39 * 60
    mono[0] += 2
    metadata = await service.probe(magnetic("9" * 40))

    assert len(lt.sessions) == 2
    assert metadata.info_hash == "9" * 40
    assert len(lt.sessions[1].added) == 1


async def test_a_timeout_drops_the_session_for_the_next_attempt(settings: Settings) -> None:
    now = [1_000.0]
    lt = FakeLibtorrent(FakeHandle(FakeInfo(), metadata_after=10_000))
    service = make_service(settings, lt, clock=lambda: now[0], deadline=0.02)

    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("a" * 40))

    assert service.backend._session is None

    # Once the failure cache expires the next probe starts from a fresh session.
    now[0] += 5 * 60 + 1
    with pytest.raises(ProbeTimeout):
        await service.probe(magnetic("a" * 40))

    assert len(lt.sessions) == 2
