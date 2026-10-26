from __future__ import annotations

import time
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from daku.models import AnimeMapping
from daku.services.metadata import mapping


def _count(engine: Engine) -> int:
    with Session(engine) as session:
        return int(session.scalar(select(func.count()).select_from(AnimeMapping)) or 0)


def test_ingest_populates_rows_and_coverage(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
    mapping_payload: dict[str, Any],
) -> None:
    engine, factory, report = ingested

    assert report.rows == _count(engine) > 0
    assert report.rows == len(mapping.flatten_mappings(mapping_payload))
    assert report.schema_version == "3.0.3"
    assert report.generated_on == "2026-10-23T06:49:48Z"
    assert report.coverage == {
        "imdb_show": 1,
        "anilist": 1,
        "mal": 0,
        "tvdb_show": 1,
    }

    with factory() as session:
        status = mapping.mapping_status(session)
        assert status.status == "ok"
        assert status.row_count == report.rows
        assert status.stale is False
        assert status.coverage == report.coverage


def test_ingest_compressed_zstd(
    db: tuple[Engine, sessionmaker[Session]],
    mapping_payload: dict[str, Any],
    compress: Any,
) -> None:
    engine, _factory = db

    report = mapping.ingest_compressed(engine, compress(mapping_payload))

    assert report.rows > 0
    assert report.source == "download"


def test_corrupt_download_leaves_previous_dataset_intact(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
) -> None:
    engine, factory, report = ingested
    before = _count(engine)
    assert before == report.rows

    with pytest.raises(mapping.MappingError) as excinfo:
        mapping.ingest_compressed(engine, b"not a zstd frame")

    assert excinfo.value.code == "MAPPING_CORRUPT"
    assert _count(engine) == before
    with factory() as session:
        assert mapping.mapping_status(session).last_update == report.last_update


def test_ingest_is_atomic_when_a_late_row_is_bad(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
) -> None:
    engine, factory, report = ingested
    before = _count(engine)

    broken = {
        "$meta": {"schema_version": "3.0.3"},
        "anilist:1": {"imdb_show:tt1:s1": {"1-2": "1-2"}},
        "anilist:2": {"imdb_show:tt2:s1": {"bogus": "1-2"}},
    }
    with pytest.raises(mapping.MappingError):
        mapping.ingest_payload(engine, broken)

    assert _count(engine) == before
    with factory() as session:
        assert mapping.mapping_status(session).last_update == report.last_update


def test_ingest_rejects_unsupported_schema(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, _factory = db
    payload = {"$meta": {"schema_version": "2.1.0"}, "anilist:1": {}}

    with pytest.raises(mapping.MappingError) as excinfo:
        mapping.ingest_payload(engine, payload)

    assert excinfo.value.code == "MAPPING_UNSUPPORTED_SCHEMA"
    assert _count(engine) == 0


def test_ingest_40k_entries_under_60_seconds(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    engine, _factory = db
    payload: dict[str, Any] = {"$meta": {"schema_version": "3.0.3"}}
    for index in range(1, 40_001):
        payload[f"anilist:{index}"] = {f"mal:{100_000 + index}": {"1-12": "1-12"}}

    started = time.monotonic()
    report = mapping.ingest_payload(engine, payload)
    elapsed = time.monotonic() - started

    assert report.rows == 80_000
    assert elapsed < 60
