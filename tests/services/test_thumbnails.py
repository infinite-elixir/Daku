from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.errors import AppError
from daku.models import MediaFile, Title
from daku.repositories import media_files as media_files_repo
from daku.services.thumbnails import THUMBNAIL_UNAVAILABLE, ThumbnailService
from daku.utils import new_id, utc_now_iso

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeProcess:
    def __init__(self, argv: tuple[str, ...], return_code: int, *, hang: bool) -> None:
        self.argv = argv
        self.returncode = return_code
        self._hang = hang

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(60)
        if self.returncode == 0:
            Path(self.argv[-1]).write_bytes(JPEG)
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


def make_row(tmp_path: Path, *, duration_s: float | None = 100.0) -> MediaFile:
    source = tmp_path / "episode.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x" * 32)
    return MediaFile(
        id=new_id(),
        title_id="title-1",
        src_path=str(source),
        size_bytes=32,
        duration_s=duration_s,
        is_primary=True,
        created_at=utc_now_iso(),
    )


def make_service(
    settings: Settings,
    *,
    runner: Any = None,
    which: Any = None,
    max_concurrent: int = 2,
    timeout: float = 10.0,
    clock: Any = None,
) -> ThumbnailService:
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    return ThumbnailService(
        settings,
        ffmpeg_bin="ffmpeg",
        timeout=timeout,
        max_concurrent=max_concurrent,
        which=which or (lambda _name: "/usr/bin/ffmpeg"),
        runner=runner,
        **kwargs,
    )


async def test_cache_hit_skips_ffmpeg(settings: Settings, tmp_path: Path) -> None:
    row = make_row(tmp_path)
    calls: list[tuple[str, ...]] = []

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        calls.append(argv)
        return FakeProcess(argv, 0, hang=False)

    service = make_service(settings, runner=runner)
    service.cache_dir.mkdir(parents=True)
    service.cache_path(row.id).write_bytes(JPEG)

    result = await service.get(row)

    assert result.path == service.cache_path(row.id)
    assert calls == []


async def test_extracts_a_frame_and_uses_the_duration_seek(
    settings: Settings, tmp_path: Path
) -> None:
    row = make_row(tmp_path, duration_s=100.0)
    seen: list[tuple[str, ...]] = []

    async def runner(*argv: str, **kwargs: Any) -> FakeProcess:
        seen.append(argv)
        assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
        return FakeProcess(argv, 0, hang=False)

    service = make_service(settings, runner=runner)
    result = await service.get(row)

    assert result.path.is_file()
    assert result.path.read_bytes() == JPEG
    assert result.etag == f"{row.id}:32:{int(Path(row.src_path).stat().st_mtime)}"
    argv = seen[0]
    assert argv[1:3] == ("-y", "-ss")
    assert argv[3] == "25.000"
    assert argv[4] == "-i"
    assert argv[5].endswith("episode.mkv")
    assert argv[6:10] == ("-frames:v", "1", "-vf", "scale=960:-2")
    assert argv[10:12] == ("-q:v", "4")
    assert not list(service.cache_dir.glob("*.tmp.jpg"))


async def test_non_positive_duration_falls_back_to_sixty_seconds(
    settings: Settings, tmp_path: Path
) -> None:
    row = make_row(tmp_path, duration_s=0.0)
    seen: list[tuple[str, ...]] = []

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        seen.append(argv)
        return FakeProcess(argv, 0, hang=False)

    await make_service(settings, runner=runner).get(row)

    assert seen[0][3] == "60.000"


async def test_missing_source_file_is_a_404(settings: Settings, tmp_path: Path) -> None:
    row = make_row(tmp_path)
    Path(row.src_path).unlink()

    service = make_service(settings)

    with pytest.raises(AppError) as caught:
        await service.get(row)

    assert caught.value.code == THUMBNAIL_UNAVAILABLE
    assert caught.value.status_code == 404


async def test_missing_ffmpeg_is_a_503_and_cached(settings: Settings, tmp_path: Path) -> None:
    row = make_row(tmp_path)
    service = make_service(settings, which=lambda _name: None)

    with pytest.raises(AppError) as caught:
        await service.get(row)
    assert caught.value.code == THUMBNAIL_UNAVAILABLE
    assert caught.value.status_code == 503

    # The failure cache answers before `which` is consulted again.
    service._which = lambda _name: pytest.fail("ffmpeg should not be probed twice")
    with pytest.raises(AppError) as again:
        await service.get(row)
    assert again.value.status_code == 503


async def test_failed_extraction_is_cached_for_five_minutes(
    settings: Settings, tmp_path: Path
) -> None:
    row = make_row(tmp_path)
    calls: list[int] = []
    now = [1000.0]

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        calls.append(1)
        return FakeProcess(argv, 1, hang=False)

    service = make_service(settings, runner=runner, clock=lambda: now[0])

    with pytest.raises(AppError):
        await service.get(row)
    with pytest.raises(AppError):
        await service.get(row)
    assert len(calls) == 1

    now[0] += 301
    with pytest.raises(AppError):
        await service.get(row)
    assert len(calls) == 2


async def test_timeout_is_a_404_and_cleans_the_temporary_file(
    settings: Settings, tmp_path: Path
) -> None:
    row = make_row(tmp_path)

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(argv, 0, hang=True)

    service = make_service(settings, runner=runner, timeout=0.01)

    with pytest.raises(AppError) as caught:
        await service.get(row)

    assert caught.value.status_code == 404
    assert not list(service.cache_dir.glob("*.tmp.jpg"))
    assert not service.cache_path(row.id).exists()


async def test_empty_output_is_a_failure(settings: Settings, tmp_path: Path) -> None:
    row = make_row(tmp_path)

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        process = FakeProcess(argv, 0, hang=False)
        process.communicate = lambda: _empty(process)  # type: ignore[method-assign]
        return process

    async def _empty(process: FakeProcess) -> tuple[bytes, bytes]:
        Path(process.argv[-1]).write_bytes(b"")
        return b"", b""

    service = make_service(settings, runner=runner)

    with pytest.raises(AppError):
        await service.get(row)


async def test_concurrency_is_capped(settings: Settings, tmp_path: Path) -> None:
    rows = [make_row(tmp_path / str(index)) for index in range(4)]

    active = 0
    peak = 0

    async def runner(*argv: str, **_kwargs: Any) -> FakeProcess:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        process = FakeProcess(argv, 0, hang=False)
        await process.communicate()
        active -= 1
        return process

    service = make_service(settings, runner=runner, max_concurrent=2)

    await asyncio.gather(*(service.get(row) for row in rows))

    assert peak == 2


# --- router -----------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def _media_row(client: TestClient, tmp_path: Path) -> MediaFile:
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
        source = tmp_path / "bbb.mkv"
        source.write_bytes(b"x" * 32)
        return media_files_repo.create_media_file(
            session,
            title_id=title.id,
            src_path=str(source),
            size_bytes=32,
        )


def test_thumbnail_sends_etag_and_304_on_match(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    row = _media_row(client, tmp_path)
    service = make_service(settings)
    service.cache_dir.mkdir(parents=True)
    service.cache_path(row.id).write_bytes(JPEG)
    client.app.state.thumbnails = service
    etag = service.etag(row)

    first = client.get(f"/api/media-files/{row.id}/thumbnail")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert first.headers["etag"] == etag
    assert first.headers["cache-control"] == "private, max-age=86400"
    assert first.content == JPEG

    second = client.get(f"/api/media-files/{row.id}/thumbnail", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


def test_thumbnail_404s_when_the_file_is_gone(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    row = _media_row(client, tmp_path)
    client.app.state.thumbnails = make_service(settings)
    Path(row.src_path).unlink()

    response = client.get(f"/api/media-files/{row.id}/thumbnail")

    assert response.status_code == 404
    assert response.json()["code"] == THUMBNAIL_UNAVAILABLE


def test_thumbnail_404s_for_unknown_media_file(client: TestClient, settings: Settings) -> None:
    client.app.state.thumbnails = make_service(settings)

    response = client.get("/api/media-files/nope/thumbnail")

    assert response.status_code == 404
    assert response.json()["code"] == THUMBNAIL_UNAVAILABLE
