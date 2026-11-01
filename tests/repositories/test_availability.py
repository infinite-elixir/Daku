from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_files_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata.models import EpisodeRef


def _movie_with_file(
    session: Session,
    path: Path,
    *,
    size_bytes: int | None = None,
    lib_path: str | None = None,
) -> str:
    title = titles_repo.create_title(session, kind="movie", title="Movie")
    media_files_repo.create_media_file(
        session,
        title_id=title.id,
        src_path=str(path),
        lib_path=lib_path,
        size_bytes=size_bytes,
        is_primary=True,
    )
    return title.id


def _series_with_files(session: Session, paths: list[Path]) -> str:
    title = titles_repo.create_title(session, kind="series", title="Show")
    episodes_repo.sync_episodes(
        session,
        title_id=title.id,
        episodes=[EpisodeRef(season=1, episode=index + 1) for index in range(len(paths))],
    )
    for index, path in enumerate(paths):
        episode = episodes_repo.find_episode(
            session, title_id=title.id, season=1, episode=index + 1
        )
        assert episode is not None
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=episode.id,
            src_path=str(path),
            is_primary=True,
        )
    return title.id


def test_availability_tracks_files_on_disk(
    db: tuple[object, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    first = tmp_path / "one.mkv"
    second = tmp_path / "two.mkv"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    with factory() as session:
        title_id = _series_with_files(session, [first, second])
        assert titles_repo.availability_map(session, [title_id])[title_id] == "complete"
        assert len(titles_repo.available_episode_ids(session, title_id)) == 2

        second.unlink()
        assert titles_repo.availability_map(session, [title_id])[title_id] == "partial"
        assert len(titles_repo.available_episode_ids(session, title_id)) == 1
        assert titles_repo.missing_file_count(session, title_id) == 1

        first.unlink()
        assert titles_repo.availability_map(session, [title_id])[title_id] == "none"
        assert titles_repo.available_episode_ids(session, title_id) == set()
        assert titles_repo.missing_file_count(session, title_id) == 2


def test_library_copy_keeps_a_title_available_when_source_is_gone(
    db: tuple[object, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    source = tmp_path / "download.mkv"
    library = tmp_path / "library.mkv"
    library.write_bytes(b"video")

    with factory() as session:
        title_id = _movie_with_file(session, source, lib_path=str(library))
        assert titles_repo.availability_map(session, [title_id])[title_id] == "complete"


def test_size_mismatch_counts_as_missing(
    db: tuple[object, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    media_file = tmp_path / "truncated.mkv"
    media_file.write_bytes(b"short")

    with factory() as session:
        title_id = _movie_with_file(session, media_file, size_bytes=999)
        assert titles_repo.availability_map(session, [title_id])[title_id] == "none"
        assert titles_repo.missing_file_count(session, title_id) == 1


def test_missing_lib_path_does_not_fall_back_to_src(
    db: tuple[object, sessionmaker[Session]], tmp_path: Path
) -> None:
    _engine, factory = db
    source = tmp_path / "download.mkv"
    source.write_bytes(b"video")
    library = tmp_path / "library.mkv"

    with factory() as session:
        title_id = _movie_with_file(session, source, lib_path=str(library))
        assert titles_repo.availability_map(session, [title_id])[title_id] == "none"
