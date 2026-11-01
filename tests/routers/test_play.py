from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.errors import AppError
from daku.models import Title
from daku.repositories import media_files as media_files_repo
from daku.services.player.mpv import LaunchResult
from daku.utils import new_id, utc_now_iso


class StubPlayer:
    def __init__(self, *, binary: str | None = "/usr/bin/mpv") -> None:
        self._binary = binary
        self.calls: list[tuple[str, Path]] = []
        self.failure: AppError | None = None

    @property
    def binary(self) -> str | None:
        return self._binary

    async def play(self, media_file_id: str, path: Path) -> LaunchResult:
        self.calls.append((media_file_id, path))
        if self.failure is not None:
            raise self.failure
        return LaunchResult(media_file_id=media_file_id, launched=True)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def _media(client: TestClient, path: Path, *, lib_path: Path | None = None) -> str:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        title = Title(
            id=new_id(),
            kind="movie",
            title="Big Buck Bunny",
            metadata_source="cinemeta",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.commit()
        row = media_files_repo.create_media_file(
            session,
            title_id=title.id,
            src_path=str(path),
            lib_path=str(lib_path) if lib_path is not None else None,
            size_bytes=path.stat().st_size if path.exists() else None,
        )
        return row.id


def _library_file(settings: Settings, name: str = "Big Buck Bunny.mkv") -> Path:
    settings.movies_dir.mkdir(parents=True, exist_ok=True)
    path = settings.movies_dir / name
    path.write_bytes(b"x" * 16)
    return path


def _enable(client: TestClient, player: StubPlayer | None = None) -> StubPlayer:
    client.app.state.settings.mpv_enabled = True
    stub = player or StubPlayer()
    client.app.state.mpv_player = stub
    return stub


def test_play_launches_the_resolved_library_path(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    stub = _enable(client)

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 202
    assert response.json() == {"media_file_id": media_id, "launched": True}
    assert stub.calls == [(media_id, path.resolve())]


def test_play_prefers_lib_path_over_src_path(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    download = tmp_path / "download.mkv"
    download.write_bytes(b"x" * 16)
    library = _library_file(settings)
    media_id = _media(client, download, lib_path=library)
    stub = _enable(client)

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 202
    assert stub.calls[0][1] == library.resolve()


def test_disabled_flag_returns_409(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    stub = _enable(client)
    client.app.state.settings.mpv_enabled = False

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 409
    assert response.json()["code"] == "MPV_DISABLED"
    assert stub.calls == []


def test_unknown_media_file_returns_404(client: TestClient) -> None:
    _enable(client)

    response = client.post("/api/media-files/does-not-exist/play")

    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_FILE_NOT_FOUND"


def test_missing_file_returns_404(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    path.unlink()
    _enable(client)

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_FILE_MISSING"


def test_download_dir_is_playable_before_the_organiser_runs(
    client: TestClient, settings: Settings
) -> None:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    download = settings.downloads_dir / "Backrooms.mkv"
    download.write_bytes(b"x" * 16)
    media_id = _media(client, download)
    stub = _enable(client)

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 202
    assert stub.calls == [(media_id, download.resolve())]


def test_path_outside_the_library_returns_403(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.mkv"
    outside.write_bytes(b"x" * 16)
    media_id = _media(client, outside)
    stub = _enable(client)

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 403
    assert response.json()["code"] == "PATH_OUTSIDE_LIBRARY"
    assert stub.calls == []


def test_extra_body_is_ignored(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    stub = _enable(client)

    response = client.post(
        f"/api/media-files/{media_id}/play",
        json={"path": "/etc/passwd", "args": ["--script=/tmp/evil.lua"]},
    )

    assert response.status_code == 202
    assert stub.calls == [(media_id, path.resolve())]


def test_missing_binary_returns_503(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    _enable(client, StubPlayer(binary=None))

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 503
    assert response.json()["code"] == "MPV_UNAVAILABLE"


def test_early_failure_is_surfaced(client: TestClient, settings: Settings) -> None:
    path = _library_file(settings)
    media_id = _media(client, path)
    stub = _enable(client)
    stub.failure = AppError(
        code="MPV_FAILED",
        message="Failed to open display",
        status_code=502,
        detail={"exit_code": 1},
    )

    response = client.post(f"/api/media-files/{media_id}/play")

    assert response.status_code == 502
    assert response.json()["code"] == "MPV_FAILED"
    assert response.json()["message"] == "Failed to open display"


async def test_relaunch_kills_only_the_tracked_child(settings: Settings, tmp_path: Path) -> None:
    """End-to-end with the real player and a stubbed child process."""

    from daku.services.player.mpv import MpvPlayer

    class Child:
        def __init__(self) -> None:
            import asyncio

            self.returncode: int | None = None
            self.terminated = False
            self._exited = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self._exited.wait()
            return b"", b""

        async def wait(self) -> int:
            await self._exited.wait()
            assert self.returncode is not None
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self._exited.set()

        def kill(self) -> None:
            self.returncode = -9
            self._exited.set()

    children: list[Child] = []

    async def runner(*_args: object, **_kwargs: object) -> Child:
        child = Child()
        children.append(child)
        return child

    player = MpvPlayer(
        settings,
        runner=runner,
        which=lambda _name: "/usr/bin/mpv",
        ipc_connect_seconds=0.01,
    )
    await player.play("first", tmp_path / "one.mkv")
    await player.play("second", tmp_path / "two.mkv")
    await player.aclose()

    assert len(children) == 2
    assert children[0].terminated is True
