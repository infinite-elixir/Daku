from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.db import MIGRATIONS, SCHEMA_VERSION, create_db_engine, current_version, migrate
from daku.models import Episode, Title
from daku.repositories import episodes as episodes_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata.models import EpisodeRef


def _upgrade_to(engine: Engine, version: int) -> None:
    for migration in MIGRATIONS:
        if migration.version > version:
            break
        migration.upgrade(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"PRAGMA user_version = {migration.version}")


def test_migration_15_rewrites_only_untouched_defaults(settings: Settings) -> None:
    from daku.db import _migration_015
    from daku.repositories import settings as settings_repo

    engine = create_db_engine(settings.db_path)
    try:
        _upgrade_to(engine, 14)
        factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        with factory() as session:
            settings_repo.set_setting(
                session,
                "torrentio_config",
                "providers=yts,eztv,1337x,thepiratebay,torrentgalaxy"
                "|qualityfilter=scr,cam,unknown|sort=seeders",
            )
            settings_repo.set_setting(
                session,
                "providers_movie",
                ["yts", "eztv", "1337x", "thepiratebay", "torrentgalaxy"],
            )
            settings_repo.set_setting(session, "providers_series", ["yts"])

        _migration_015(engine)

        with factory() as session:
            assert settings_repo.get_setting(session, "torrentio_config") == "sort=seeders"
            assert settings_repo.get_setting(session, "providers_movie") == []
            # User intent is untouched.
            assert settings_repo.get_setting(session, "providers_series") == ["yts"]
    finally:
        engine.dispose()


def test_watch_columns_upgrade_existing_rows_as_untracked(
    settings: Settings,
) -> None:
    """A v13 database upgrades without guessing any watch state."""

    engine = create_db_engine(settings.db_path)
    try:
        for migration in MIGRATIONS:
            if migration.version > 13:
                break
            migration.upgrade(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(f"PRAGMA user_version = {migration.version}")

        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                INSERT INTO titles (id, kind, title, added_at)
                VALUES ('title-old', 'series', 'Old Show', '2026-01-01T00:00:00Z')
                """
            )
            conn.exec_driver_sql(
                """
                INSERT INTO episodes (id, title_id, season, episode, status)
                VALUES ('ep-old', 'title-old', 1, 1, 'missing')
                """
            )

        assert current_version(engine) == 13
        assert migrate(engine) == SCHEMA_VERSION == 15

        factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        with factory() as session:
            title = session.get(Title, "title-old")
            episode = session.get(Episode, "ep-old")
            assert title is not None and episode is not None
            assert title.watch_status is None
            assert title.status_changed_at is None
            assert episode.watched is False
            assert episode.watched_at is None
            assert episodes_repo.progress_for(session, title.id) == {
                "watched_episode_count": 0,
                "watchable_episode_count": 1,
                "next_episode": {"season": 1, "episode": 1, "name": None},
            }
    finally:
        engine.dispose()


def test_watch_progress_cascades_with_title(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Cascade")
        episodes_repo.sync_episodes(
            session,
            title_id=title.id,
            episodes=[EpisodeRef(season=1, episode=1)],
        )
        episode = episodes_repo.list_episodes(session, title.id)[0]
        episodes_repo.set_episode_watched(session, title=title, episode=episode, watched=True)
        title_id = title.id
        episode_id = episode.id

        assert titles_repo.delete_title(session, title_id) is True
        session.expire_all()
        assert session.get(Title, title_id) is None
        assert session.get(Episode, episode_id) is None


def test_explicit_completed_bulk_marks_only_non_specials(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        title = titles_repo.create_title(session, kind="anime", title="Bulk")
        episodes_repo.sync_episodes(
            session,
            title_id=title.id,
            episodes=[
                EpisodeRef(season=0, episode=1),
                EpisodeRef(season=1, episode=1),
                EpisodeRef(season=1, episode=2),
            ],
        )

        episodes_repo.set_title_watch_status(
            session, title=title, status="completed", clear_progress=False
        )

        assert title.watch_status == "completed"
        assert title.status_changed_at is not None
        rows = episodes_repo.list_episodes(session, title.id)
        assert [row.watched for row in rows] == [False, True, True]
        assert all(row.watched_at is not None for row in rows if row.season != 0)
        assert all(row.watched_at is None for row in rows if row.season == 0)
