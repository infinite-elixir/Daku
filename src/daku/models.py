"""SQLAlchemy models for the Daku v1 schema."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from daku.utils import new_id, utc_now_iso


class Base(DeclarativeBase):
    pass


class Title(Base):
    __tablename__ = "titles"
    __table_args__ = (
        Index(
            "ux_titles_imdb_id",
            "imdb_id",
            unique=True,
            sqlite_where=text("imdb_id IS NOT NULL"),
        ),
        Index(
            "ux_titles_anilist_id",
            "anilist_id",
            unique=True,
            sqlite_where=text("anilist_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    tmdb_id: Mapped[str | None] = mapped_column(String(32))
    tvdb_id: Mapped[str | None] = mapped_column(String(32))
    anilist_id: Mapped[int | None] = mapped_column(Integer)
    mal_id: Mapped[int | None] = mapped_column(Integer)
    overview: Mapped[str | None] = mapped_column(Text)
    # JSON array of every known release-search spelling (T-053).
    aliases_json: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    # Wide 16:9 art for the title banner. Optional: older rows fall back to the poster.
    banner_url: Mapped[str | None] = mapped_column(Text)
    folder_name: Mapped[str | None] = mapped_column(Text)
    metadata_source: Mapped[str | None] = mapped_column(String(16))
    metadata_synced_at: Mapped[str | None] = mapped_column(String(40))
    added_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    wanted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    wanted_at: Mapped[str | None] = mapped_column(String(40))

    episodes: Mapped[list[Episode]] = relationship(
        back_populates="title", cascade="all, delete-orphan", passive_deletes=True
    )
    releases: Mapped[list[Release]] = relationship(
        back_populates="title", cascade="all, delete-orphan", passive_deletes=True
    )
    media_files: Mapped[list[MediaFile]] = relationship(
        back_populates="title", cascade="all, delete-orphan", passive_deletes=True
    )
    telegram_messages: Mapped[list[TelegramMessage]] = relationship(
        back_populates="title", cascade="all, delete-orphan", passive_deletes=True
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (Index("ix_episodes_title_season_episode", "title_id", "season", "episode"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(Text)
    air_date: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="missing", server_default=text("'missing'")
    )

    title: Mapped[Title] = relationship(back_populates="episodes")
    releases: Mapped[list[Release]] = relationship(
        back_populates="episode", cascade="all, delete-orphan", passive_deletes=True
    )
    media_files: Mapped[list[MediaFile]] = relationship(
        back_populates="episode", cascade="all, delete-orphan", passive_deletes=True
    )


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint("info_hash", "episode_id", name="uq_releases_info_hash_episode"),
        Index(
            "ux_releases_info_hash_episode_nullsafe",
            "info_hash",
            func.coalesce(text("episode_id"), text("''")),
            unique=True,
        ),
        Index("ix_releases_title_episode", "title_id", "episode_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("episodes.id", ondelete="CASCADE")
    )
    provider: Mapped[str | None] = mapped_column(String(64))
    info_hash: Mapped[str | None] = mapped_column(String(64))
    magnet: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    seeders: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    is_saved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    saved_at: Mapped[str | None] = mapped_column(String(40))
    last_checked_at: Mapped[str | None] = mapped_column(String(40))
    last_seen_seeders: Mapped[int | None] = mapped_column(Integer)
    miss_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dead: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    fetched_at: Mapped[str | None] = mapped_column(String(40))

    title: Mapped[Title] = relationship(back_populates="releases")
    episode: Mapped[Episode | None] = relationship(back_populates="releases")
    downloads: Mapped[list[Download]] = relationship(back_populates="release")


class Download(Base):
    __tablename__ = "downloads"
    __table_args__ = (
        UniqueConstraint("engine", "external_id", name="uq_downloads_engine_external_id"),
        Index("ix_downloads_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    release_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("releases.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default=text("'queued'")
    )
    progress: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    downloaded_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_bytes: Mapped[int | None] = mapped_column(Integer)
    speed_bps: Mapped[int | None] = mapped_column(Integer)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    save_path: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    selected_files_json: Mapped[str | None] = mapped_column(Text)
    file_match_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    release: Mapped[Release | None] = relationship(back_populates="downloads")
    media_files: Mapped[list[MediaFile]] = relationship(back_populates="download")


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (Index("ix_media_files_download_id", "download_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    download_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("downloads.id", ondelete="CASCADE")
    )
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("episodes.id", ondelete="CASCADE")
    )
    src_path: Mapped[str] = mapped_column(Text, nullable=False)
    lib_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    container: Mapped[str | None] = mapped_column(String(32))
    vcodec: Mapped[str | None] = mapped_column(String(64))
    acodec: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    playback_position_s: Mapped[float | None] = mapped_column(Float)
    playback_duration_s: Mapped[float | None] = mapped_column(Float)
    playback_updated_at: Mapped[str | None] = mapped_column(String(40))
    sidecars_json: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    download: Mapped[Download | None] = relationship(back_populates="media_files")
    title: Mapped[Title] = relationship(back_populates="media_files")
    episode: Mapped[Episode | None] = relationship(back_populates="media_files")


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_telegram_channel_message"),
        Index("ix_telegram_messages_file_unique_id", "file_unique_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE")
    )
    episode_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("episodes.id", ondelete="CASCADE")
    )
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(128), nullable=False)
    file_name: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="archived", server_default=text("'archived'")
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    title: Mapped[Title | None] = relationship(back_populates="telegram_messages")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class TitleSeason(Base):
    """One library season and the provider season that supplies its structure."""

    __tablename__ = "title_seasons"

    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    library_season: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_season: Mapped[int] = mapped_column(Integer, nullable=False)
    anilist_id: Mapped[int | None] = mapped_column(Integer)
    absolute_offset: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TitleCour(Base):
    """One AniList cour inside a library season (T-052 split-cour identity)."""

    __tablename__ = "title_cours"
    __table_args__ = (Index("ix_title_cours_title_season", "title_id", "library_season"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    library_season: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_season: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    end_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    anilist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute_offset: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TitleOverride(Base):
    """The durable ``the user said so`` record for one editable field."""

    __tablename__ = "title_overrides"

    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    field: Mapped[str] = mapped_column(String(32), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TitleSeasonOffset(Base):
    __tablename__ = "title_season_offsets"

    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    provider_season: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_season: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TitleEpisodeOverride(Base):
    """One user renumbering: a library episode row -> a new target position."""

    __tablename__ = "title_episode_overrides"
    __table_args__ = (
        UniqueConstraint("title_id", "season", "episode", name="uq_title_episode_override"),
        Index("ix_title_episode_overrides_title", "title_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    target_season: Mapped[int] = mapped_column(Integer, nullable=False)
    target_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class AnimeMapping(Base):
    """One flattened AniBridge range row.

    ``src_end`` / ``tgt_end`` use ``0`` for an open-ended range because SQLite
    primary keys cannot contain NULL. Episode numbers are always ``>= 1``.
    """

    __tablename__ = "anime_mappings"
    __table_args__ = (
        Index("ix_anime_mappings_src", "src_provider", "src_id", "src_scope", "src_start"),
        Index("ix_anime_mappings_tgt", "tgt_provider", "tgt_id", "tgt_scope", "tgt_start"),
    )

    src_provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    src_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    src_scope: Mapped[str] = mapped_column(
        String(16), primary_key=True, default="", server_default=text("''")
    )
    src_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    src_end: Mapped[int] = mapped_column(Integer, primary_key=True)
    tgt_provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    tgt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tgt_scope: Mapped[str] = mapped_column(
        String(16), primary_key=True, default="", server_default=text("''")
    )
    tgt_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    tgt_end: Mapped[int] = mapped_column(Integer, primary_key=True)
    ratio: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1, server_default=text("1")
    )


class AnimeMappingOverride(Base):
    """A user-picked episode mapping. Always wins over the dataset."""

    __tablename__ = "anime_mapping_overrides"
    __table_args__ = (
        UniqueConstraint("title_id", "season", "episode", name="uq_anime_override_title_episode"),
        Index("ix_anime_mapping_overrides_title", "title_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    target_provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="anilist", server_default=text("'anilist'")
    )
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_season: Mapped[int | None] = mapped_column(Integer)
    target_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_ts", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="info", server_default=text("'info'")
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text)

    def payload(self) -> dict[str, Any]:
        import json

        if not self.payload_json:
            return {}
        try:
            value = json.loads(self.payload_json)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
