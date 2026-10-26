from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daku.services.metadata import mapping


@pytest.fixture
def ingested(
    db: tuple[Engine, sessionmaker[Session]], mapping_payload: dict[str, Any]
) -> tuple[Engine, sessionmaker[Session], mapping.IngestReport]:
    engine, factory = db
    report = mapping.ingest_payload(engine, mapping_payload)
    return engine, factory, report


@pytest.fixture
def session(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
) -> Iterator[Session]:
    _engine, factory, _report = ingested
    with factory() as db_session:
        yield db_session
