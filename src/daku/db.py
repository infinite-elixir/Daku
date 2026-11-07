"""SQLite engine, schema migrations and the session dependency.

Migrations are append-only: never edit a shipped step, add the next one. The current
schema version is stored in ``PRAGMA user_version``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.models import Base

MigrationFn = Callable[[Engine], None]


def _migration_001(engine: Engine) -> None:
    statements = [
        """
        CREATE TABLE titles (
            id TEXT PRIMARY KEY NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            imdb_id TEXT,
            tmdb_id TEXT,
            tvdb_id TEXT,
            anilist_id INTEGER,
            mal_id INTEGER,
            overview TEXT,
            poster_url TEXT,
            added_at TEXT NOT NULL,
            wanted INTEGER NOT NULL DEFAULT 0,
            wanted_at TEXT
        )
        """,
        "CREATE INDEX ix_titles_imdb_id ON titles (imdb_id)",
        """
        CREATE TABLE episodes (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            absolute INTEGER,
            name TEXT,
            air_date TEXT,
            status TEXT NOT NULL DEFAULT 'missing'
        )
        """,
        "CREATE INDEX ix_episodes_title_season_episode ON episodes (title_id, season, episode)",
        """
        CREATE TABLE releases (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES episodes (id) ON DELETE CASCADE,
            provider TEXT,
            info_hash TEXT,
            magnet TEXT,
            filename TEXT,
            quality TEXT,
            size_bytes INTEGER,
            seeders INTEGER,
            metadata_json TEXT,
            is_saved INTEGER NOT NULL DEFAULT 0,
            saved_at TEXT,
            last_checked_at TEXT,
            last_seen_seeders INTEGER,
            miss_count INTEGER NOT NULL DEFAULT 0,
            dead INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT,
            CONSTRAINT uq_releases_info_hash_episode UNIQUE (info_hash, episode_id)
        )
        """,
        (
            "CREATE UNIQUE INDEX ux_releases_info_hash_episode_nullsafe "
            "ON releases (info_hash, COALESCE(episode_id, ''))"
        ),
        "CREATE INDEX ix_releases_title_episode ON releases (title_id, episode_id)",
        """
        CREATE TABLE downloads (
            id TEXT PRIMARY KEY NOT NULL,
            release_id TEXT REFERENCES releases (id) ON DELETE SET NULL,
            source TEXT NOT NULL,
            engine TEXT NOT NULL,
            external_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            progress REAL NOT NULL DEFAULT 0,
            downloaded_bytes INTEGER NOT NULL DEFAULT 0,
            total_bytes INTEGER,
            speed_bps INTEGER,
            eta_seconds INTEGER,
            save_path TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT uq_downloads_engine_external_id UNIQUE (engine, external_id)
        )
        """,
        "CREATE INDEX ix_downloads_status ON downloads (status)",
        """
        CREATE TABLE media_files (
            id TEXT PRIMARY KEY NOT NULL,
            download_id TEXT REFERENCES downloads (id) ON DELETE CASCADE,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES episodes (id) ON DELETE CASCADE,
            src_path TEXT NOT NULL,
            lib_path TEXT,
            size_bytes INTEGER,
            container TEXT,
            vcodec TEXT,
            acodec TEXT,
            width INTEGER,
            height INTEGER,
            duration_s REAL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX ix_media_files_download_id ON media_files (download_id)",
        """
        CREATE TABLE telegram_messages (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT REFERENCES titles (id) ON DELETE CASCADE,
            episode_id TEXT REFERENCES episodes (id) ON DELETE CASCADE,
            channel_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            file_name TEXT,
            file_size INTEGER,
            mime_type TEXT,
            state TEXT NOT NULL DEFAULT 'archived',
            created_at TEXT NOT NULL,
            CONSTRAINT uq_telegram_channel_message UNIQUE (channel_id, message_id)
        )
        """,
        "CREATE INDEX ix_telegram_messages_file_unique_id ON telegram_messages (file_unique_id)",
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            kind TEXT NOT NULL,
            payload_json TEXT
        )
        """,
        "CREATE INDEX ix_events_ts ON events (ts)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_002(engine: Engine) -> None:
    """T-005: flattened AniBridge mappings plus title-scoped manual overrides."""

    statements = [
        """
        CREATE TABLE anime_mappings (
            src_provider TEXT NOT NULL,
            src_id TEXT NOT NULL,
            src_scope TEXT NOT NULL DEFAULT '',
            tgt_provider TEXT NOT NULL,
            tgt_id TEXT NOT NULL,
            tgt_scope TEXT NOT NULL DEFAULT '',
            src_start INTEGER NOT NULL,
            src_end INTEGER NOT NULL,
            tgt_start INTEGER NOT NULL,
            tgt_end INTEGER NOT NULL,
            ratio INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (
                src_provider, src_id, src_scope, src_start, src_end,
                tgt_provider, tgt_id, tgt_scope, tgt_start, tgt_end, ratio
            )
        ) WITHOUT ROWID
        """,
        (
            "CREATE INDEX ix_anime_mappings_src "
            "ON anime_mappings (src_provider, src_id, src_scope, src_start)"
        ),
        (
            "CREATE INDEX ix_anime_mappings_tgt "
            "ON anime_mappings (tgt_provider, tgt_id, tgt_scope, tgt_start)"
        ),
        """
        CREATE TABLE anime_mapping_overrides (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            target_provider TEXT NOT NULL DEFAULT 'anilist',
            target_id TEXT NOT NULL,
            target_season INTEGER,
            target_episode INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            CONSTRAINT uq_anime_override_title_episode UNIQUE (title_id, season, episode)
        )
        """,
        ("CREATE INDEX ix_anime_mapping_overrides_title ON anime_mapping_overrides (title_id)"),
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_004(engine: Engine) -> None:
    """T-022: metadata source and sync time for titles."""
    statements = [
        "ALTER TABLE titles ADD COLUMN metadata_source TEXT",
        "ALTER TABLE titles ADD COLUMN metadata_synced_at TEXT",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_003(engine: Engine) -> None:
    """T-006: user-facing folder name plus one title per IMDb/AniList id."""

    statements = [
        "ALTER TABLE titles ADD COLUMN folder_name TEXT",
        ("CREATE UNIQUE INDEX ux_titles_imdb_id ON titles (imdb_id) WHERE imdb_id IS NOT NULL"),
        (
            "CREATE UNIQUE INDEX ux_titles_anilist_id ON titles (anilist_id) "
            "WHERE anilist_id IS NOT NULL"
        ),
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_005(engine: Engine) -> None:
    """T-010: keep the direct-download URL so a failed aria2 row can be retried."""

    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE downloads ADD COLUMN url TEXT")


def _migration_006(engine: Engine) -> None:
    """T-012: keep detected subtitle sidecars next to their media file."""

    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE media_files ADD COLUMN sidecars_json TEXT")


def _migration_007(engine: Engine) -> None:
    """T-029: durable user overrides for identity, season offsets and episodes."""

    statements = [
        """
        CREATE TABLE title_overrides (
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            field TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (title_id, field)
        )
        """,
        """
        CREATE TABLE title_episode_overrides (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            target_season INTEGER NOT NULL,
            target_episode INTEGER NOT NULL,
            absolute INTEGER,
            created_at TEXT NOT NULL,
            CONSTRAINT uq_title_episode_override UNIQUE (title_id, season, episode)
        )
        """,
        ("CREATE INDEX ix_title_episode_overrides_title ON title_episode_overrides (title_id)"),
        """
        CREATE TABLE title_season_offsets (
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            provider_season INTEGER NOT NULL,
            library_season INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (title_id, provider_season)
        )
        """,
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_008(engine: Engine) -> None:
    """T-037: per-season provider structure for anime and split-cour titles."""

    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE title_seasons (
                title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
                library_season INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                provider_season INTEGER NOT NULL,
                anilist_id INTEGER,
                absolute_offset INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (title_id, library_season)
            )
            """
        )


def _migration_009(engine: Engine) -> None:
    """T-031: persist the chosen engine file indices and the matcher result."""

    statements = [
        "ALTER TABLE downloads ADD COLUMN selected_files_json TEXT",
        "ALTER TABLE downloads ADD COLUMN file_match_json TEXT",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_010(engine: Engine) -> None:
    """T-035: keep a wide banner/backdrop URL next to the portrait poster."""

    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE titles ADD COLUMN banner_url TEXT")


def _migration_011(engine: Engine) -> None:
    """T-052: split-cour AniList entries inside one library season."""

    statements = [
        """
        CREATE TABLE title_cours (
            id TEXT PRIMARY KEY NOT NULL,
            title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
            library_season INTEGER NOT NULL,
            provider_season INTEGER NOT NULL DEFAULT 1,
            start_episode INTEGER NOT NULL,
            end_episode INTEGER NOT NULL,
            anilist_id INTEGER NOT NULL,
            absolute_offset INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX ix_title_cours_title_season ON title_cours (title_id, library_season)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_012(engine: Engine) -> None:
    """T-053: persist every known release-search alias per title."""

    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE titles ADD COLUMN aliases_json TEXT")


def _migration_013(engine: Engine) -> None:
    """T-054: remember how far the host player got through a media file."""

    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE media_files ADD COLUMN playback_position_s REAL")
        conn.exec_driver_sql("ALTER TABLE media_files ADD COLUMN playback_duration_s REAL")
        conn.exec_driver_sql("ALTER TABLE media_files ADD COLUMN playback_updated_at TEXT")


def _migration_014(engine: Engine) -> None:
    """T-032: local watch status on titles plus per-episode progress."""

    statements = [
        "ALTER TABLE titles ADD COLUMN watch_status TEXT",
        "ALTER TABLE titles ADD COLUMN status_changed_at TEXT",
        "ALTER TABLE episodes ADD COLUMN watched INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE episodes ADD COLUMN watched_at TEXT",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def _migration_015(engine: Engine) -> None:
    """T-059: move untouched provider defaults to "all providers".

    Only the exact shipped defaults are rewritten. Any other stored value is user
    intent and stays untouched. Values are JSON encoded by ``repositories.settings``.
    """

    old_providers = ["yts", "eztv", "1337x", "thepiratebay", "torrentgalaxy"]
    old_config = "providers=yts,eztv,1337x,thepiratebay,torrentgalaxy|qualityfilter=scr,cam,unknown|sort=seeders"
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("SELECT key, value FROM settings").all()
        for key, raw in rows:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if key == "torrentio_config" and value == old_config:
                conn.exec_driver_sql(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (json.dumps("sort=seeders"), key),
                )
            elif key in {"providers_movie", "providers_series", "providers_anime"} and (
                value == old_providers
            ):
                conn.exec_driver_sql(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (json.dumps([]), key),
                )


class Migration:
    __slots__ = ("name", "upgrade", "version")

    def __init__(self, version: int, name: str, upgrade: MigrationFn) -> None:
        self.version = version
        self.name = name
        self.upgrade = upgrade


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial schema", _migration_001),
    Migration(2, "anibridge mappings", _migration_002),
    Migration(3, "library folder name and title identities", _migration_003),
    Migration(4, "metadata source and sync time for titles", _migration_004),
    Migration(5, "direct download source url", _migration_005),
    Migration(6, "media file subtitle sidecars", _migration_006),
    Migration(7, "manual metadata overrides", _migration_007),
    Migration(8, "per-season provider structure", _migration_008),
    Migration(9, "selective torrent file downloads", _migration_009),
    Migration(10, "wide banner art for title pages", _migration_010),
    Migration(11, "split-cour anime identities", _migration_011),
    Migration(12, "release-search aliases", _migration_012),
    Migration(13, "media file playback progress", _migration_013),
    Migration(14, "local watch status and episode progress", _migration_014),
    Migration(15, "all-provider Torrentio defaults", _migration_015),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


def create_db_engine(db_path: Path) -> Engine:
    """Create a SQLite engine with the pragmas every connection needs."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return engine


def current_version(engine: Engine) -> int:
    with engine.connect() as conn:
        value = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
    return int(value)


def migrate(engine: Engine) -> int:
    """Apply pending migrations and return the resulting schema version."""

    version = current_version(engine)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} is newer than this build ({SCHEMA_VERSION})"
        )
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        migration.upgrade(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"PRAGMA user_version = {migration.version}")
        version = migration.version
    return version


def init_db(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    """Ensure directories exist, migrate the DB and seed default settings."""

    settings.ensure_dirs()
    engine = create_db_engine(settings.db_path)
    migrate(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        from daku.repositories.settings import seed_default_settings

        seed_default_settings(session, settings)
    return engine, factory


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""

    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def dump_database(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    """Return every table as a list of row dictionaries (CLI ``db dump``)."""

    dump: dict[str, list[dict[str, Any]]] = {}
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table in inspector.get_table_names():
            result = conn.exec_driver_sql(f'SELECT * FROM "{table}"')
            dump[table] = [dict(row._mapping) for row in result]
    return dump


def dump_database_json(engine: Engine) -> str:
    return json.dumps(dump_database(engine), indent=2, ensure_ascii=False, default=str)


def table_names(engine: Engine) -> list[str]:
    return sorted(inspect(engine).get_table_names())


def metadata_tables() -> list[str]:
    """Table names declared on the ORM metadata (used by schema tests)."""

    from daku.models import Base as _Base

    return sorted(_Base.metadata.tables)


__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "Base",
    "create_db_engine",
    "current_version",
    "dump_database",
    "dump_database_json",
    "get_session",
    "init_db",
    "metadata_tables",
    "migrate",
    "table_names",
    "text",
]
