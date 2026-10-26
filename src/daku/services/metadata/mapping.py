"""AniBridge ID and episode mapping (T-005).

The published ``anibridge-mappings`` dataset is a nested JSON object::

    {source descriptor: {target descriptor: {source range: target range}}}

Descriptors are ``provider:id[:scope]``. This module flattens that structure into
``anime_mappings`` rows, synthesises the reverse direction when the dataset does not
already contain it, and answers episode lookups from SQLite.

Range rules:

* a source range is one contiguous range ``x[-y]``;
* a target range may contain comma-separated segments and an optional ``|ratio``;
* ``ratio = n`` means one source episode spans ``n`` target episodes;
* ``ratio = -n`` means ``n`` source episodes share one target episode.

Open-ended ranges are stored as ``0`` end values because SQLite primary keys cannot
contain ``NULL``. Episode numbers are always ``>= 1``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import zstandard
from sqlalchemy import Engine, insert, or_, select
from sqlalchemy.orm import Session

from daku.config import Settings
from daku.models import AnimeMapping, AnimeMappingOverride, Title
from daku.repositories.settings import get_setting, set_setting
from daku.utils import new_id, utc_now_iso

DATASET_URL = (
    "https://github.com/anibridge/anibridge-mappings/releases/download/v3/mappings.json.zst"
)
SUPPORTED_SCHEMA_MAJOR = "3"
REFRESH_DAYS_DEFAULT = 7
PROVIDERS = frozenset(
    {
        "anidb",
        "anilist",
        "imdb_movie",
        "imdb_show",
        "mal",
        "tmdb_movie",
        "tmdb_show",
        "tvdb_movie",
        "tvdb_show",
    }
)
COVERAGE_PROVIDERS = ("imdb_show", "anilist", "mal", "tvdb_show")

SETTING_URL = "anime_mapping_url"
SETTING_REFRESH_DAYS = "anime_mapping_refresh_days"
SETTING_LAST_UPDATE = "anime_mapping_last_update"
SETTING_SCHEMA_VERSION = "anime_mapping_schema_version"
SETTING_GENERATED_ON = "anime_mapping_generated_on"
SETTING_ROW_COUNT = "anime_mapping_row_count"
SETTING_COVERAGE = "anime_mapping_coverage"

MAPPING_SETTING_DEFAULTS: dict[str, Any] = {
    SETTING_URL: DATASET_URL,
    SETTING_REFRESH_DAYS: REFRESH_DAYS_DEFAULT,
}

_DESCRIPTOR_RE = re.compile(r"^(?P<provider>[a-z][a-z0-9_]*):(?P<id>[^:]+)(?::(?P<scope>[^:]+))?$")
_SOURCE_RANGE_RE = re.compile(r"^(?P<start>\d+)(?:-(?P<end>\d*))?$")


class MappingError(Exception):
    """The dataset is missing, corrupt or uses an unsupported schema."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Descriptor:
    provider: str
    id: str
    scope: str = ""


class MappingRow(NamedTuple):
    src_provider: str
    src_id: str
    src_scope: str
    src_start: int
    src_end: int
    tgt_provider: str
    tgt_id: str
    tgt_scope: str
    tgt_start: int
    tgt_end: int
    ratio: int


@dataclass(slots=True)
class FlattenStats:
    coverage: dict[str, set[str]] = field(
        default_factory=lambda: {provider: set() for provider in COVERAGE_PROVIDERS}
    )

    def coverage_counts(self) -> dict[str, int]:
        return {provider: len(ids) for provider, ids in self.coverage.items()}


@dataclass(slots=True)
class IngestReport:
    rows: int
    coverage: dict[str, int]
    schema_version: str
    generated_on: str | None
    last_update: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "coverage": self.coverage,
            "schema_version": self.schema_version,
            "generated_on": self.generated_on,
            "last_update": self.last_update,
            "source": self.source,
        }


@dataclass(slots=True)
class EpisodeMapping:
    source_provider: str
    source_id: str
    source_scope: str
    source_episode: int
    target_provider: str
    target_id: str
    target_scope: str
    target_episode: int
    target_episode_end: int
    ratio: int
    override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": {
                "provider": self.source_provider,
                "id": self.source_id,
                "scope": self.source_scope or None,
                "episode": self.source_episode,
            },
            "target": {
                "provider": self.target_provider,
                "id": self.target_id,
                "scope": self.target_scope or None,
                "episode": self.target_episode,
                "episode_end": self.target_episode_end,
            },
            "ratio": self.ratio,
            "override": self.override,
        }


@dataclass(slots=True)
class MappingStatus:
    status: str
    row_count: int
    stale: bool
    last_update: str | None
    last_update_age_days: float | None
    refresh_days: int
    schema_version: str | None
    generated_on: str | None
    coverage: dict[str, int]
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "row_count": self.row_count,
            "stale": self.stale,
            "last_update": self.last_update,
            "last_update_age_days": self.last_update_age_days,
            "refresh_days": self.refresh_days,
            "schema_version": self.schema_version,
            "generated_on": self.generated_on,
            "coverage": self.coverage,
            "url": self.url,
        }


# --------------------------------------------------------------------------- parsing


def parse_descriptor(value: str) -> Descriptor | None:
    """Parse ``provider:id[:scope]``. Unknown providers return ``None``."""

    match = _DESCRIPTOR_RE.match(value.strip())
    if match is None:
        return None
    provider = match.group("provider")
    identifier = match.group("id").strip()
    if provider not in PROVIDERS or not identifier:
        return None
    return Descriptor(provider=provider, id=identifier, scope=(match.group("scope") or "").strip())


def descriptor_string(descriptor: Descriptor) -> str:
    if descriptor.scope:
        return f"{descriptor.provider}:{descriptor.id}:{descriptor.scope}"
    return f"{descriptor.provider}:{descriptor.id}"


def parse_source_range(value: str) -> tuple[int, int | None]:
    """Parse one contiguous source range ``x``, ``x-y`` or ``x-``."""

    match = _SOURCE_RANGE_RE.match(value.strip())
    if match is None:
        raise MappingError("MAPPING_BAD_RANGE", f"invalid episode range: {value!r}")
    start = int(match.group("start"))
    raw_end = match.group("end")
    if start < 1:
        raise MappingError("MAPPING_BAD_RANGE", f"episode ranges are 1-based: {value!r}")
    if raw_end is None:
        return start, start
    if raw_end == "":
        return start, None
    end = int(raw_end)
    if end < start:
        raise MappingError("MAPPING_BAD_RANGE", f"range ends before it starts: {value!r}")
    return start, end


def parse_target_ranges(value: str) -> tuple[list[tuple[int, int | None]], int]:
    """Parse ``x[-y][,x[-y]...][|ratio]`` into segments and a ratio."""

    text = value.strip()
    ratio = 1
    if "|" in text:
        text, raw_ratio = text.rsplit("|", 1)
        try:
            ratio = int(raw_ratio)
        except ValueError as exc:
            raise MappingError("MAPPING_BAD_RANGE", f"invalid ratio: {value!r}") from exc
        if ratio == 0:
            # The published dataset contains ``|0`` on a handful of one-to-one
            # movie mappings. It carries no ratio information, so treat it as 1:1.
            ratio = 1
    if not text:
        raise MappingError("MAPPING_BAD_RANGE", f"missing target range: {value!r}")
    parts = text.split(",")
    if any(not part.strip() for part in parts):
        raise MappingError("MAPPING_BAD_RANGE", f"empty target range segment: {value!r}")
    segments = [parse_source_range(part) for part in parts]
    if not segments:
        raise MappingError("MAPPING_BAD_RANGE", f"missing target range: {value!r}")
    return segments, ratio


def dataset_meta(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MappingError("MAPPING_BAD_DATASET", "dataset must be a JSON object")
    meta = payload.get("$meta")
    if not isinstance(meta, dict):
        raise MappingError("MAPPING_BAD_DATASET", "dataset is missing $meta")
    version = meta.get("schema_version")
    if not isinstance(version, str) or not version:
        raise MappingError("MAPPING_BAD_DATASET", "dataset is missing $meta.schema_version")
    if version.split(".", 1)[0] != SUPPORTED_SCHEMA_MAJOR:
        raise MappingError(
            "MAPPING_UNSUPPORTED_SCHEMA",
            f"dataset schema {version} is not supported (want {SUPPORTED_SCHEMA_MAJOR}.x)",
        )
    if not _mapping_entries(payload):
        raise MappingError("MAPPING_BAD_DATASET", "dataset contains no mapping entries")
    generated_on = meta.get("generated_on")
    return {
        "schema_version": version,
        "generated_on": generated_on if isinstance(generated_on, str) else None,
    }


def _mapping_entries(payload: dict[str, Any]) -> list[str]:
    return [key for key in payload if not key.startswith("$")]


def _open_end(value: int | None) -> int:
    return 0 if value is None else value


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)


def _expansion(
    source: Descriptor,
    target: Descriptor,
    src_start: int,
    src_end: int | None,
    segments: list[tuple[int, int | None]],
    ratio: int,
) -> Iterator[MappingRow]:
    """Split one mapping entry into flattened source/target sub-ranges."""

    ordinal = 0
    for tgt_start, tgt_end in segments:
        length = None if tgt_end is None else tgt_end - tgt_start + 1
        if length is not None and length <= 0:
            continue
        if ratio > 0:
            sub_start = src_start + ordinal // ratio
            sub_end = None if length is None else src_start + _ceil_div(ordinal + length, ratio) - 1
        else:
            span = -ratio
            sub_start = src_start + ordinal * span
            sub_end = None if length is None else src_start + (ordinal + length) * span - 1

        if src_end is not None and sub_start > src_end:
            break
        if sub_end is not None and src_end is not None:
            sub_end = min(sub_end, src_end)

        if sub_end is None or sub_start <= sub_end:
            yield MappingRow(
                source.provider,
                source.id,
                source.scope,
                sub_start,
                _open_end(sub_end),
                target.provider,
                target.id,
                target.scope,
                tgt_start,
                _open_end(tgt_end),
                ratio,
            )

        if length is None:
            break
        ordinal += length


def iter_flattened(payload: Any, stats: FlattenStats | None = None) -> Iterator[MappingRow]:
    """Yield flattened rows, including synthesised reverse rows.

    Reverse rows are only synthesised when the dataset does not already contain the
    opposite descriptor pair, so an explicit dataset reverse always wins.
    """

    meta = dataset_meta(payload)
    del meta
    assert isinstance(payload, dict)
    stats = stats if stats is not None else FlattenStats()

    parsed_pairs: list[tuple[Descriptor, Descriptor, dict[str, Any]]] = []
    pairs: set[tuple[str, str]] = set()
    for raw_source in _mapping_entries(payload):
        source = parse_descriptor(raw_source)
        targets = payload.get(raw_source)
        if source is None or not isinstance(targets, dict):
            continue
        if source.provider in stats.coverage:
            stats.coverage[source.provider].add(source.id)
        for raw_target, ranges in targets.items():
            target = parse_descriptor(raw_target)
            if target is None or not isinstance(ranges, dict):
                continue
            parsed_pairs.append((source, target, ranges))
            pairs.add((descriptor_string(source), descriptor_string(target)))

    for source, target, ranges in parsed_pairs:
        for raw_source_range, raw_target_range in ranges.items():
            if not isinstance(raw_target_range, str):
                continue
            src_start, src_end = parse_source_range(raw_source_range)
            segments, ratio = parse_target_ranges(raw_target_range)
            for row in _expansion(source, target, src_start, src_end, segments, ratio):
                yield row

        if (descriptor_string(target), descriptor_string(source)) in pairs:
            continue

        for raw_source_range, raw_target_range in ranges.items():
            if not isinstance(raw_target_range, str):
                continue
            src_start, src_end = parse_source_range(raw_source_range)
            segments, ratio = parse_target_ranges(raw_target_range)
            for row in _expansion(source, target, src_start, src_end, segments, ratio):
                yield MappingRow(
                    row.tgt_provider,
                    row.tgt_id,
                    row.tgt_scope,
                    row.tgt_start,
                    row.tgt_end,
                    row.src_provider,
                    row.src_id,
                    row.src_scope,
                    row.src_start,
                    row.src_end,
                    -row.ratio,
                )


def flatten_mappings(payload: Any) -> list[MappingRow]:
    """Return every flattened row. Intended for tests and small datasets."""

    return list(iter_flattened(payload))


# --------------------------------------------------------------------------- ingest


def decompress_zstd(data: bytes) -> bytes:
    try:
        decompressor = zstandard.ZstdDecompressor()
        return decompressor.decompress(data)
    except zstandard.ZstdError:
        try:
            with zstandard.ZstdDecompressor().stream_reader(data) as reader:
                return reader.read()
        except zstandard.ZstdError as exc:
            raise MappingError("MAPPING_CORRUPT", f"cannot decompress dataset: {exc}") from exc


def decode_dataset(data: bytes) -> Any:
    raw = decompress_zstd(data)
    if not raw:
        raise MappingError("MAPPING_CORRUPT", "dataset is empty")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MappingError("MAPPING_CORRUPT", f"dataset is not valid JSON: {exc}") from exc


def _upsert_setting_conn(conn: Any, key: str, value: Any) -> None:
    conn.exec_driver_sql(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False, default=str)),
    )


def ingest_payload(engine: Engine, payload: Any, *, source: str = "local") -> IngestReport:
    """Validate, then replace every mapping row in a single transaction."""

    meta = dataset_meta(payload)
    stats = FlattenStats()
    now = utc_now_iso()

    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM anime_mappings")
        batch: list[dict[str, Any]] = []
        statement = insert(AnimeMapping).prefix_with("OR IGNORE")
        for row in iter_flattened(payload, stats):
            batch.append(row._asdict())
            if len(batch) >= 1000:
                conn.execute(statement, batch)
                batch.clear()
        if batch:
            conn.execute(statement, batch)

        total = int(conn.exec_driver_sql("SELECT COUNT(*) FROM anime_mappings").scalar_one())
        coverage = stats.coverage_counts()
        for key, value in {
            SETTING_LAST_UPDATE: now,
            SETTING_SCHEMA_VERSION: meta["schema_version"],
            SETTING_GENERATED_ON: meta["generated_on"],
            SETTING_ROW_COUNT: total,
            SETTING_COVERAGE: coverage,
        }.items():
            _upsert_setting_conn(conn, key, value)

    return IngestReport(
        rows=total,
        coverage=coverage,
        schema_version=meta["schema_version"],
        generated_on=meta["generated_on"],
        last_update=now,
        source=source,
    )


def ingest_compressed(engine: Engine, data: bytes, *, source: str = "download") -> IngestReport:
    payload = decode_dataset(data)
    return ingest_payload(engine, payload, source=source)


def ingest_file(engine: Engine, path: Path) -> IngestReport:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MappingError("MAPPING_MISSING", f"cannot read mapping dataset: {exc}") from exc
    return ingest_compressed(engine, data, source=str(path))


def dataset_path(settings: Settings) -> Path:
    return settings.data_dir / "mappings" / "anibridge.json.zst"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


async def fetch_dataset(client: httpx.AsyncClient, url: str) -> bytes:
    try:
        response = await client.get(url, timeout=60.0)
    except httpx.HTTPError as exc:
        raise MappingError("MAPPING_UNAVAILABLE", f"dataset download failed: {exc}") from exc
    if response.status_code != 200:
        raise MappingError(
            "MAPPING_UNAVAILABLE", f"dataset download failed: HTTP {response.status_code}"
        )
    if not response.content:
        raise MappingError("MAPPING_UNAVAILABLE", "dataset download was empty")
    return response.content


async def refresh(
    engine: Engine,
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    url: str | None = None,
    persist: bool = True,
) -> IngestReport:
    """Download and ingest the dataset. The previous dataset survives every failure."""

    dataset_url = url or DATASET_URL
    data = await fetch_dataset(client, dataset_url)
    # Ingest before replacing the on-disk copy: a corrupt download must leave the
    # previous file and rows untouched.
    report = await asyncio.to_thread(ingest_compressed, engine, data, source="download")
    if persist:
        await asyncio.to_thread(_write_atomic, dataset_path(settings), data)
    with Session(engine) as session:
        set_setting(session, SETTING_URL, dataset_url)
    return report


# --------------------------------------------------------------------------- status


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def refresh_days(session: Session) -> int:
    value = get_setting(session, SETTING_REFRESH_DAYS, REFRESH_DAYS_DEFAULT)
    try:
        days = int(value)
    except (TypeError, ValueError):
        return REFRESH_DAYS_DEFAULT
    return max(1, days)


def last_update(session: Session) -> datetime | None:
    return _parse_timestamp(get_setting(session, SETTING_LAST_UPDATE))


def is_stale(session: Session, now: datetime | None = None) -> bool:
    updated = last_update(session)
    if updated is None:
        return True
    current = now or datetime.now(UTC)
    return current - updated > timedelta(days=refresh_days(session))


def mapping_status(session: Session, now: datetime | None = None) -> MappingStatus:
    updated = last_update(session)
    row_count = int(get_setting(session, SETTING_ROW_COUNT, 0) or 0)
    stale = is_stale(session, now)
    if row_count == 0:
        status = "missing"
    elif stale:
        status = "degraded"
    else:
        status = "ok"
    age_days: float | None = None
    if updated is not None:
        age_days = round(((now or datetime.now(UTC)) - updated).total_seconds() / 86400, 2)
    coverage = get_setting(session, SETTING_COVERAGE, {})
    if not isinstance(coverage, dict):
        coverage = {}
    return MappingStatus(
        status=status,
        row_count=row_count,
        stale=stale,
        last_update=get_setting(session, SETTING_LAST_UPDATE, None),
        last_update_age_days=age_days,
        refresh_days=refresh_days(session),
        schema_version=get_setting(session, SETTING_SCHEMA_VERSION, None),
        generated_on=get_setting(session, SETTING_GENERATED_ON, None),
        coverage={provider: int(coverage.get(provider, 0)) for provider in COVERAGE_PROVIDERS},
        url=str(get_setting(session, SETTING_URL, DATASET_URL)),
    )


def ensure_mapping_settings(session: Session) -> None:
    for key, value in MAPPING_SETTING_DEFAULTS.items():
        if get_setting(session, key) is None:
            set_setting(session, key, value)


# --------------------------------------------------------------------------- lookup


def _target_episode(row: AnimeMapping, episode: int) -> tuple[int, int] | None:
    offset = episode - row.src_start
    if offset < 0:
        return None
    if row.ratio > 0:
        relative = offset * row.ratio
        span = row.ratio
    else:
        relative = offset // (-row.ratio)
        span = 1
    target = row.tgt_start + relative
    if row.tgt_end and target > row.tgt_end:
        return None
    end = target + span - 1
    if row.tgt_end:
        end = min(end, row.tgt_end)
    return target, end


def resolve_episode(
    session: Session,
    *,
    source_provider: str,
    source_id: str,
    source_scope: str,
    episode: int,
    target_provider: str = "anilist",
    target_id: str | None = None,
    target_scope: str | None = None,
) -> EpisodeMapping | None:
    """Forward lookup ``(provider, id, scope, episode) -> target``."""

    if episode < 1:
        return None
    statement = (
        select(AnimeMapping)
        .where(
            AnimeMapping.src_provider == source_provider,
            AnimeMapping.src_id == str(source_id),
            AnimeMapping.src_scope == source_scope,
            AnimeMapping.src_start <= episode,
            or_(AnimeMapping.src_end == 0, AnimeMapping.src_end >= episode),
            AnimeMapping.tgt_provider == target_provider,
        )
        .order_by(AnimeMapping.tgt_start, AnimeMapping.tgt_end, AnimeMapping.ratio)
        .limit(1)
    )
    if target_id is not None:
        statement = statement.where(AnimeMapping.tgt_id == str(target_id))
    if target_scope is not None:
        statement = statement.where(AnimeMapping.tgt_scope == target_scope)

    row = session.scalar(statement)
    if row is None:
        return None
    resolved = _target_episode(row, episode)
    if resolved is None:
        return None
    target_episode, target_episode_end = resolved
    return EpisodeMapping(
        source_provider=source_provider,
        source_id=str(source_id),
        source_scope=source_scope,
        source_episode=episode,
        target_provider=row.tgt_provider,
        target_id=row.tgt_id,
        target_scope=row.tgt_scope,
        target_episode=target_episode,
        target_episode_end=target_episode_end,
        ratio=row.ratio,
    )


def anilist_id_for_season(
    session: Session,
    *,
    source_provider: str,
    source_id: str,
    season: int,
) -> int | None:
    """The AniList entry that owns one provider season, from AniBridge."""

    statement = (
        select(AnimeMapping.tgt_id)
        .where(
            AnimeMapping.src_provider == source_provider,
            AnimeMapping.src_id == str(source_id),
            AnimeMapping.src_scope == f"s{season}",
            AnimeMapping.tgt_provider == "anilist",
        )
        .order_by(AnimeMapping.tgt_start)
        .limit(1)
    )
    value = session.scalar(statement)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lookup_imdb_to_anilist(
    session: Session,
    imdb_id: str,
    season: int,
    episode: int,
    *,
    anilist_id: int | str | None = None,
) -> EpisodeMapping | None:
    for provider, scope in (
        ("imdb_show", f"s{season}"),
        ("imdb_movie", ""),
        ("imdb", ""),
        ("imdb_id", ""),
    ):
        mapped = resolve_episode(
            session,
            source_provider=provider,
            source_id=imdb_id,
            source_scope=scope,
            episode=episode,
            target_provider="anilist",
            target_id=None if anilist_id is None else str(anilist_id),
        )
        if mapped is not None:
            return mapped
    return None


def lookup_anilist_to_imdb(
    session: Session,
    anilist_id: int | str,
    episode: int,
) -> EpisodeMapping | None:
    if episode < 1:
        return None
    statement = (
        select(AnimeMapping)
        .where(
            AnimeMapping.src_provider == "anilist",
            AnimeMapping.src_id == str(anilist_id),
            AnimeMapping.src_scope == "",
            AnimeMapping.src_start <= episode,
            or_(AnimeMapping.src_end == 0, AnimeMapping.src_end >= episode),
            AnimeMapping.tgt_provider.in_(("imdb_show", "tvdb_show")),
        )
        .order_by(AnimeMapping.tgt_provider, AnimeMapping.tgt_start, AnimeMapping.ratio)
        .limit(1)
    )
    row = session.scalar(statement)
    if row is None:
        return None
    resolved = _target_episode(row, episode)
    if resolved is None:
        return None
    target_episode, target_episode_end = resolved
    return EpisodeMapping(
        source_provider="anilist",
        source_id=str(anilist_id),
        source_scope="",
        source_episode=episode,
        target_provider=row.tgt_provider,
        target_id=row.tgt_id,
        target_scope=row.tgt_scope,
        target_episode=target_episode,
        target_episode_end=target_episode_end,
        ratio=row.ratio,
    )


# --------------------------------------------------------------------------- overrides


def find_override(
    session: Session, title_id: str, season: int, episode: int
) -> AnimeMappingOverride | None:
    return session.scalar(
        select(AnimeMappingOverride)
        .where(
            AnimeMappingOverride.title_id == title_id,
            AnimeMappingOverride.season == season,
            AnimeMappingOverride.episode == episode,
        )
        .limit(1)
    )


def write_override(
    session: Session,
    *,
    title: Title,
    season: int,
    episode: int,
    target_episode: int,
    target_provider: str = "anilist",
    target_id: str | None = None,
    target_season: int | None = None,
) -> AnimeMappingOverride:
    """Create or replace the user's mapping for one title episode."""

    if season < 0 or episode < 1 or target_episode < 1:
        raise MappingError("MAPPING_BAD_RANGE", "season/episode numbers must be positive")
    resolved_target = target_id or (
        str(title.anilist_id) if target_provider == "anilist" and title.anilist_id else None
    )
    if not resolved_target:
        raise MappingError("MAPPING_NO_TARGET", "title has no target id for this provider")
    row = find_override(session, title.id, season, episode)
    if row is None:
        row = AnimeMappingOverride(
            id=new_id(),
            title_id=title.id,
            season=season,
            episode=episode,
            created_at=utc_now_iso(),
        )
        session.add(row)
    row.target_provider = target_provider
    row.target_id = str(resolved_target)
    row.target_season = target_season
    row.target_episode = target_episode
    session.commit()
    session.refresh(row)
    return row


def override_mapping(title: Title, row: AnimeMappingOverride) -> EpisodeMapping:
    if title.imdb_id:
        provider, identifier = "imdb_show", title.imdb_id
    elif title.tvdb_id:
        provider, identifier = "tvdb_show", title.tvdb_id
    else:
        provider, identifier = "anilist", str(title.anilist_id or "")
    return EpisodeMapping(
        source_provider=provider,
        source_id=identifier,
        source_scope=f"s{row.season}",
        source_episode=row.episode,
        target_provider=row.target_provider,
        target_id=row.target_id,
        target_scope="" if row.target_season is None else f"s{row.target_season}",
        target_episode=row.target_episode,
        target_episode_end=row.target_episode,
        ratio=1,
        override=True,
    )


__all__: tuple[str, ...] = (
    "COVERAGE_PROVIDERS",
    "DATASET_URL",
    "MAPPING_SETTING_DEFAULTS",
    "Descriptor",
    "EpisodeMapping",
    "FlattenStats",
    "IngestReport",
    "MappingError",
    "MappingRow",
    "MappingStatus",
    "decode_dataset",
    "decompress_zstd",
    "descriptor_string",
    "ensure_mapping_settings",
    "fetch_dataset",
    "find_override",
    "flatten_mappings",
    "ingest_compressed",
    "ingest_file",
    "ingest_payload",
    "is_stale",
    "iter_flattened",
    "lookup_anilist_to_imdb",
    "lookup_imdb_to_anilist",
    "mapping_status",
    "override_mapping",
    "parse_descriptor",
    "parse_source_range",
    "parse_target_ranges",
    "refresh",
    "resolve_episode",
    "write_override",
)
