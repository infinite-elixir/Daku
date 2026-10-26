from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daku.errors import AppError
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.organize import select
from daku.services.organize.ffprobe import MediaInfo

MB = 1024 * 1024


def make_file(path: Path, size: int = 60 * MB) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


def candidate(
    path: Path, root: Path, size: int = 60 * MB, *, video: bool = True
) -> select.Candidate:
    info = MediaInfo(vcodec="h264", acodec="aac", width=1920, height=1080) if video else MediaInfo()
    return select.Candidate(
        path=path,
        size_bytes=size,
        info=info,
        is_sample=select.is_sample_path(path, root) or size < select.PRIMARY_MIN_BYTES,
    )


def test_scan_finds_video_files_only(tmp_path: Path) -> None:
    make_file(tmp_path / "movie.mkv")
    make_file(tmp_path / "notes.txt", 10)
    make_file(tmp_path / "subs.srt", 10)

    found = select.scan_video_files(tmp_path)

    assert found == [tmp_path / "movie.mkv"]


def test_largest_valid_file_becomes_primary(tmp_path: Path) -> None:
    small = candidate(tmp_path / "small.mkv", tmp_path, 60 * MB)
    large = candidate(tmp_path / "large.mkv", tmp_path, 200 * MB)

    assert select.select_primary([small, large]) is large


def test_sample_never_beats_the_main_feature(tmp_path: Path) -> None:
    sample = candidate(tmp_path / "sample.mkv", tmp_path, 300 * MB)
    main = candidate(tmp_path / "Movie.2021.1080p.mkv", tmp_path, 100 * MB)

    ranked = select.rank_candidates([sample, main])

    assert ranked[0] is main
    assert select.select_primary([sample, main]) is main


def test_trailer_and_low_size_files_are_not_primary(tmp_path: Path) -> None:
    trailer = candidate(tmp_path / "trailer.mkv", tmp_path, 80 * MB)
    tiny = candidate(tmp_path / "tiny.mkv", tmp_path, 10 * MB)
    main = candidate(tmp_path / "feature.mkv", tmp_path, 90 * MB)

    assert select.select_primary([trailer, tiny, main]) is main
    assert tiny.is_sample


def test_files_under_fifty_mb_are_never_primary(tmp_path: Path) -> None:
    only = candidate(tmp_path / "short.mkv", tmp_path, 49 * MB)

    assert select.select_primary([only]) is None


def test_samples_inside_a_sample_directory_are_flagged(tmp_path: Path) -> None:
    path = tmp_path / "Sample" / "clip.mkv"
    make_file(path)

    built = select.build_candidate(path, tmp_path)

    assert built.is_sample


def test_sidecars_share_the_video_basename(tmp_path: Path) -> None:
    video = make_file(tmp_path / "Movie.mkv")
    make_file(tmp_path / "movie.SRT", 10)
    make_file(tmp_path / "Movie.en.srt", 10)
    make_file(tmp_path / "Other.srt", 10)

    sidecars = select.detect_sidecars(video)

    assert sidecars == [tmp_path / "movie.SRT"]


def _download(factory: sessionmaker[Session], root: Path) -> Download:
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="Dune")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash="a" * 40,
            filename="Dune.mkv",
        )
        return downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id="a" * 40,
            release_id=release.id,
            status="complete",
            save_path=str(root),
        )


async def _probe(path: Path, **_kwargs: Any) -> MediaInfo:
    if path.stat().st_size == 0:
        raise AppError(code="MEDIA_FILE_EMPTY", message=f"{path.name} is zero bytes")
    return MediaInfo(
        container="matroska",
        vcodec="hevc",
        acodec="dts",
        width=1920,
        height=1080,
        duration_s=5400.0,
        audio_languages=["eng"],
        subtitle_languages=["eng"],
    )


async def test_inspection_persists_one_row_per_video_file(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S01E01.mkv", 100 * MB)
    make_file(root / "Show.S01E02.mkv", 120 * MB)
    make_file(root / "Show.S01E01.srt", 10)
    download = _download(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        result = await select.inspect_download(session, row, probe_fn=_probe, max_parallel=2)
        rows = media_repo.list_by_download(session, row.id)

    assert len(rows) == 2
    assert sum(1 for item in rows if item.is_primary) == 1
    assert result.primary is not None
    assert Path(result.primary.src_path).name == "Show.S01E02.mkv"
    assert all(item.episode_id is None for item in rows)
    assert result.errors == []
    primary = next(item for item in rows if item.is_primary)
    assert media_repo.sidecars(primary) == []


async def test_inspection_records_subtitle_sidecars(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Movie.2021.mkv", 100 * MB)
    make_file(root / "Movie.2021.srt", 10)
    download = _download(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert media_repo.sidecars(files[0]) == [str(root / "Movie.2021.srt")]


async def test_inspection_is_idempotent(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Movie.mkv", 100 * MB)
    download = _download(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)
        await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert len(files) == 1


async def test_zero_byte_download_fails_with_a_readable_error(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "broken.mkv", 0)
    download = _download(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        with pytest.raises(AppError) as excinfo:
            await select.inspect_download(session, row, probe_fn=_probe)

    assert excinfo.value.code == "MEDIA_INSPECTION_FAILED"
    assert "zero bytes" in excinfo.value.message


async def test_cover_art_only_file_is_not_a_playable_download(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "cover.mkv", 60 * MB)
    download = _download(factory, root)

    async def cover_probe(_path: Path, **_kwargs: Any) -> MediaInfo:
        return MediaInfo(vcodec=None)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        with pytest.raises(AppError) as excinfo:
            await select.inspect_download(session, row, probe_fn=cover_probe)

    assert excinfo.value.code == "MEDIA_INSPECTION_FAILED"


async def test_inspection_does_not_mutate_the_source(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    source = make_file(root / "Movie.mkv", 100 * MB)
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    download = _download(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)

    assert (source.stat().st_size, source.stat().st_mtime_ns) == before
