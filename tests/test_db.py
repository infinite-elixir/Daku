from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.db import (
    SCHEMA_VERSION,
    current_version,
    dump_database,
    init_db,
    migrate,
    table_names,
)
from daku.models import (
    Download,
    Episode,
    Event,
    MediaFile,
    Release,
    TelegramMessage,
    Title,
    TitleSeason,
)
from daku.repositories import (
    downloads,
    episodes,
    events,
    media_files,
    releases,
    titles,
)
from daku.repositories import (
    settings as settings_repo,
)
from daku.utils import utc_now_iso

ALL_TABLES = {
    "titles",
    "episodes",
    "releases",
    "downloads",
    "media_files",
    "telegram_messages",
    "settings",
    "events",
    "anime_mappings",
    "anime_mapping_overrides",
    "title_overrides",
    "title_episode_overrides",
    "title_season_offsets",
    "title_seasons",
}


@pytest.fixture
def db(settings: Settings) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    engine, factory = init_db(settings)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_fresh_database_has_full_schema(db: tuple[Engine, sessionmaker[Session]]) -> None:
    engine, _factory = db

    assert current_version(engine) == SCHEMA_VERSION == 8
    assert set(table_names(engine)) == ALL_TABLES


def test_pragmas_are_applied(db: tuple[Engine, sessionmaker[Session]]) -> None:
    engine, _factory = db

    with engine.connect() as conn:
        journal = conn.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one()

    assert str(journal).lower() == "wal"
    assert int(foreign_keys) == 1


def test_migrate_is_idempotent_and_keeps_data(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, factory = db
    with factory() as session:
        titles.create_title(session, kind="movie", title="Dune", year=2021)

    migrate(engine)
    migrate(engine)

    assert current_version(engine) == SCHEMA_VERSION
    with factory() as session:
        assert _count(session, Title) == 1


def test_deleting_title_cascades(db: tuple[Engine, sessionmaker[Session]]) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="series", title="The Wire")
        episode = episodes.create_episode(session, title_id=title.id, season=1, episode=1)
        release = releases.create_release(
            session,
            title_id=title.id,
            episode_id=episode.id,
            info_hash="hash-1",
            is_saved=True,
        )
        download = downloads.create_download(
            session,
            release_id=release.id,
            source="torrentio",
            engine="qbittorrent",
            external_id="hash-1",
        )
        media_files.create_media_file(
            session,
            title_id=title.id,
            episode_id=episode.id,
            download_id=download.id,
            src_path="/tmp/show.mkv",
            is_primary=True,
        )
        session.add(
            TelegramMessage(
                title_id=title.id,
                episode_id=episode.id,
                channel_id="-1001",
                message_id=5,
                file_id="file",
                file_unique_id="uniq",
            )
        )
        session.add(
            TitleSeason(
                title_id=title.id,
                library_season=1,
                source="cinemeta",
                source_id="tt1234567",
                provider_season=1,
                absolute_offset=0,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
        )
        session.commit()
        title_id = title.id
        download_id = download.id

        assert titles.delete_title(session, title_id) is True

        assert _count(session, Episode) == 0
        assert _count(session, Release) == 0
        assert _count(session, MediaFile) == 0
        assert _count(session, TelegramMessage) == 0
        assert _count(session, TitleSeason) == 0
        session.expire_all()
        remaining = session.get(Download, download_id)
        assert remaining is not None
        assert remaining.release_id is None


def test_library_returns_title_without_children(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        titles.create_title(session, kind="anime", title="Perfect Blue", year=1997)

        rows = titles.list_library(session)

        assert [row.title for row in rows] == ["Perfect Blue"]
        assert rows[0].wanted is False
        assert _count(session, Episode) == 0
        assert _count(session, Download) == 0
        assert _count(session, MediaFile) == 0


def test_saved_release_without_download(db: tuple[Engine, sessionmaker[Session]]) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="movie", title="Dune")
        release = releases.create_release(
            session,
            title_id=title.id,
            info_hash="abc",
            magnet="magnet:?xt=urn:btih:abc",
            is_saved=True,
        )

        saved = releases.list_saved(session)

        assert [row.id for row in saved] == [release.id]
        assert release.saved_at is not None
        assert _count(session, Download) == 0


def test_download_engine_external_id_is_unique(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        downloads.create_download(
            session, source="torrentio", engine="qbittorrent", external_id="hash-1"
        )
        with pytest.raises(IntegrityError):
            downloads.create_download(
                session, source="torrentio", engine="qbittorrent", external_id="hash-1"
            )
        session.rollback()


def test_release_info_hash_per_episode_is_unique(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="series", title="Show")
        episode = episodes.create_episode(session, title_id=title.id, season=1, episode=1)
        releases.create_release(session, title_id=title.id, episode_id=episode.id, info_hash="same")
        with pytest.raises(IntegrityError):
            releases.create_release(
                session, title_id=title.id, episode_id=episode.id, info_hash="same"
            )
        session.rollback()


def test_release_info_hash_is_unique_for_movies(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="movie", title="Movie")
        releases.create_release(session, title_id=title.id, info_hash="movie-hash")
        with pytest.raises(IntegrityError):
            releases.create_release(session, title_id=title.id, info_hash="movie-hash")
        session.rollback()


def test_telegram_channel_message_is_unique(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="movie", title="Movie")
        for unique in ("first", "second"):
            session.add(
                TelegramMessage(
                    title_id=title.id,
                    channel_id="-1001",
                    message_id=7,
                    file_id=unique,
                    file_unique_id=unique,
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_set_primary_clears_siblings(db: tuple[Engine, sessionmaker[Session]]) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles.create_title(session, kind="movie", title="Movie")
        download = downloads.create_download(
            session, source="torrentio", engine="qbittorrent", external_id="h1"
        )
        first = media_files.create_media_file(
            session,
            title_id=title.id,
            download_id=download.id,
            src_path="/tmp/a.mkv",
            is_primary=True,
        )
        second = media_files.create_media_file(
            session,
            title_id=title.id,
            download_id=download.id,
            src_path="/tmp/b.mkv",
            is_primary=True,
        )

        session.expire_all()
        assert session.get(MediaFile, first.id).is_primary is False  # type: ignore[union-attr]
        assert session.get(MediaFile, second.id).is_primary is True  # type: ignore[union-attr]

        media_files.set_primary(session, first.id)
        session.expire_all()
        assert session.get(MediaFile, first.id).is_primary is True  # type: ignore[union-attr]
        assert session.get(MediaFile, second.id).is_primary is False  # type: ignore[union-attr]


def test_settings_are_seeded_without_secrets(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        values = settings_repo.all_settings(session)

        assert set(settings_repo.DEFAULT_SETTING_KEYS) <= set(values)
        assert values["port"] == 8420
        assert values["organize_mode"] == "hardlink"
        assert "qbt_pass" not in values
        assert "telegram_bot_token" not in values


def test_events_are_recorded(db: tuple[Engine, sessionmaker[Session]]) -> None:
    _engine, factory = db
    with factory() as session:
        event = events.add_event(session, "download.created", {"id": "abc"})

        assert event.id is not None
        assert events.latest_event_id(session) == event.id
        assert events.list_events(session)[0].payload() == {"id": "abc"}
        assert _count(session, Event) == 1


def test_dump_returns_every_table(db: tuple[Engine, sessionmaker[Session]]) -> None:
    engine, factory = db
    with factory() as session:
        titles.create_title(session, kind="movie", title="Dune")

    dump = dump_database(engine)

    assert set(dump) == ALL_TABLES
    assert len(dump["titles"]) == 1


def test_db_file_is_created_under_data_dir(
    settings: Settings, db: tuple[Engine, sessionmaker[Session]]
) -> None:
    assert Path(settings.db_path).exists()
