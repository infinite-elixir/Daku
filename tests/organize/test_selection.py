from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daku.errors import AppError
from daku.models import Download
from daku.repositories import downloads as downloads_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.engines.base import EngineFile
from daku.services.organize import select
from daku.services.organize.ffprobe import MediaInfo

MB = 1024 * 1024


class FakeTorrentEngine:
    def __init__(self, paths: list[str]) -> None:
        self.paths = paths

    async def files(self, _external_id: str) -> list[EngineFile]:
        return [
            EngineFile(index=index, path=path, size_bytes=0)
            for index, path in enumerate(self.paths)
        ]


def make_file(path: Path, size: int = 100 * MB) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


async def _probe(path: Path, **_kwargs: Any) -> MediaInfo:
    return MediaInfo(
        container="matroska",
        vcodec="h264",
        acodec="aac",
        width=1920,
        height=1080,
        duration_s=1800.0,
    )


def _series(factory: sessionmaker[Session], root: Path) -> tuple[str, dict[int, str], Download]:
    """Create a series with episodes 1-6 and one completed pack download."""

    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Show", imdb_id="tt1234567")
        episodes = {
            number: episodes_repo.create_episode(
                session, title_id=title.id, season=2, episode=number, name=f"Episode {number}"
            ).id
            for number in range(1, 7)
        }
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash="a" * 40,
            filename="Show.S02.1080p.mkv",
        )
        download = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id="a" * 40,
            release_id=release.id,
            status="complete",
            save_path=str(root),
        )
        return title.id, episodes, download


async def test_only_the_selected_path_is_inspected_and_filed_under_its_episode(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S02E05.mkv", 120 * MB)
    make_file(root / "Show.S02E06.mkv", 140 * MB)
    _title_id, episodes, download = _series(factory, root)
    selected = "Show.S02E05.mkv"

    with factory() as session:
        downloads_repo.update_download(
            session,
            download.id,
            selected_files_json="[1]",
            file_match_json=json.dumps(
                {"episode_id": episodes[5], "file_paths": [selected], "total_files": 2}
            ),
        )
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        result = await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert len(files) == 1
    assert Path(files[0].src_path).name == selected
    assert files[0].episode_id == episodes[5]
    assert files[0].is_primary
    assert result.primary is not None and Path(result.primary.src_path).name == selected


async def test_stale_unselected_files_on_disk_are_ignored(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S02E05.mkv", 120 * MB)
    # A file left over from a previous whole-pack attempt. It is on disk but the
    # persisted selection says only E05 is wanted now.
    make_file(root / "Show.S02E06.mkv", 140 * MB)
    _title_id, episodes, download = _series(factory, root)

    with factory() as session:
        downloads_repo.update_download(
            session,
            download.id,
            selected_files_json="[1]",
            file_match_json=json.dumps(
                {"episode_id": episodes[5], "file_paths": ["Show.S02E05.mkv"]}
            ),
        )
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert [Path(item.src_path).name for item in files] == ["Show.S02E05.mkv"]


async def test_engine_indices_are_used_when_paths_were_not_recorded(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S02E01.mkv")
    make_file(root / "Show.S02E02.mkv")
    _title_id, episodes, download = _series(factory, root)

    with factory() as session:
        downloads_repo.update_download(session, download.id, selected_files_json="[1]")
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(
            session,
            row,
            probe_fn=_probe,
            engine=FakeTorrentEngine(["Show.S02E01.mkv", "Show.S02E02.mkv"]),
        )
        files = media_repo.list_by_download(session, row.id)

    assert [Path(item.src_path).name for item in files] == ["Show.S02E02.mkv"]
    assert files[0].episode_id == episodes[2]


async def test_indices_are_never_guessed_from_scan_order(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S02E01.mkv")
    make_file(root / "Show.S02E02.mkv")
    _title_id, _episodes, download = _series(factory, root)

    with factory() as session:
        downloads_repo.update_download(session, download.id, selected_files_json="[1]")
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        with pytest.raises(AppError) as caught:
            await select.inspect_download(session, row, probe_fn=_probe)

    assert caught.value.code == "MEDIA_SELECTION_UNRESOLVED"


async def test_a_twelve_file_pack_files_only_the_selected_episode(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    names = [f"Show.S02E{number:02d}.mkv" for number in range(1, 13)]
    for name in names:
        make_file(root / "Show.S02" / name)
    _title_id, episodes, download = _series(factory, root)

    with factory() as session:
        downloads_repo.update_download(
            session,
            download.id,
            selected_files_json="[3]",
            file_match_json=json.dumps({"episode_id": episodes[4], "total_files": 12}),
        )
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(
            session,
            row,
            probe_fn=_probe,
            engine=FakeTorrentEngine([f"Show.S02/{name}" for name in names]),
        )
        files = media_repo.list_by_download(session, row.id)

    assert len(files) == 1
    assert Path(files[0].src_path).name == "Show.S02E04.mkv"
    assert files[0].episode_id == episodes[4]
    assert files[0].is_primary


async def test_a_null_selection_keeps_every_file(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show.S02E05.mkv")
    make_file(root / "Show.S02E06.mkv")
    _title_id, _episodes, download = _series(factory, root)

    with factory() as session:
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert len(files) == 2
    assert sum(1 for item in files if item.is_primary) == 1


async def test_a_single_unnumbered_episode_falls_back_to_the_release_episode(
    db: tuple[Any, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    root = tmp_path / "downloads"
    make_file(root / "Show - 17 - The Episode.mkv")
    with factory() as session:
        title = titles_repo.create_title(session, kind="anime", title="Show", anilist_id=1)
        episode = episodes_repo.create_episode(
            session, title_id=title.id, season=2, episode=5, absolute=17, name="The Episode"
        )
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash="b" * 40,
            filename="Show - 17.mkv",
            episode_id=episode.id,
        )
        download = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id="b" * 40,
            release_id=release.id,
            status="complete",
            save_path=str(root),
            selected_files_json="[0]",
            file_match_json=json.dumps(
                {"episode_id": episode.id, "file_paths": ["Show - 17 - The Episode.mkv"]}
            ),
        )
        row = downloads_repo.get_download(session, download.id)
        assert row is not None
        await select.inspect_download(session, row, probe_fn=_probe)
        files = media_repo.list_by_download(session, row.id)

    assert len(files) == 1
    assert files[0].episode_id == episode.id
