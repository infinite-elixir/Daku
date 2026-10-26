from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.models import AnimeMapping
from daku.services.metadata import mapping
from daku.services.metadata.client import make_client

URL = "https://example.test/mappings.json.zst"


def _count(engine: Engine) -> int:
    with Session(engine) as session:
        return int(session.scalar(select(func.count()).select_from(AnimeMapping)) or 0)


def test_is_stale_uses_weekly_default(
    db: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    with factory() as session:
        assert mapping.is_stale(session) is True  # never ingested

        set_last_update = mapping.set_setting
        set_last_update(session, mapping.SETTING_LAST_UPDATE, mapping.utc_now_iso())
        assert mapping.is_stale(session) is False

        stale_time = (datetime.now(UTC) - timedelta(days=8)).isoformat().replace("+00:00", "Z")
        mapping.set_setting(session, mapping.SETTING_LAST_UPDATE, stale_time)
        assert mapping.is_stale(session) is True

        mapping.set_setting(session, mapping.SETTING_REFRESH_DAYS, 30)
        assert mapping.is_stale(session) is False
        assert mapping.mapping_status(session).refresh_days == 30


@pytest.mark.asyncio
async def test_refresh_downloads_ingests_and_persists(
    db: tuple[Engine, sessionmaker[Session]],
    settings: Settings,
    mapping_payload: dict[str, Any],
    compress: Any,
) -> None:
    engine, factory = db
    data = compress(mapping_payload)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(URL).mock(return_value=httpx.Response(200, content=data))
        async with make_client() as client:
            report = await mapping.refresh(engine, settings, client, url=URL)

    assert route.call_count == 1
    assert report.rows > 0
    assert report.source == "download"
    assert mapping.dataset_path(settings).read_bytes() == data
    with factory() as session:
        status = mapping.mapping_status(session)
        assert status.status == "ok"
        assert status.url == URL


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_dataset(
    db: tuple[Engine, sessionmaker[Session]],
    settings: Settings,
    mapping_payload: dict[str, Any],
    compress: Any,
) -> None:
    engine, factory = db
    report = mapping.ingest_payload(engine, mapping_payload)
    before = _count(engine)
    assert before == report.rows

    with respx.mock(assert_all_called=False) as mock:
        mock.get(URL).mock(return_value=httpx.Response(503))
        async with make_client() as client:
            with pytest.raises(mapping.MappingError) as excinfo:
                await mapping.refresh(engine, settings, client, url=URL)

    assert excinfo.value.code == "MAPPING_UNAVAILABLE"
    assert _count(engine) == before
    with factory() as session:
        assert mapping.mapping_status(session).last_update == report.last_update


@pytest.mark.asyncio
async def test_corrupt_refresh_keeps_previous_dataset_and_file(
    db: tuple[Engine, sessionmaker[Session]],
    settings: Settings,
    mapping_payload: dict[str, Any],
    compress: Any,
) -> None:
    engine, _factory = db
    good = compress(mapping_payload)
    path = mapping.dataset_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(good)
    report = mapping.ingest_compressed(engine, good)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(URL).mock(return_value=httpx.Response(200, content=b"garbage"))
        async with make_client() as client:
            with pytest.raises(mapping.MappingError):
                await mapping.refresh(engine, settings, client, url=URL)

    assert path.read_bytes() == good
    assert _count(engine) == report.rows


def test_doctor_reports_degraded_without_dataset(
    client: TestClient,
) -> None:
    response = client.get("/api/system/doctor")

    assert response.status_code == 200
    checks = {check["name"]: check for check in response.json()["checks"]}
    assert checks["mapping"]["status"] == "degraded"
    assert "falls back" in checks["mapping"]["detail"]


def test_doctor_reports_degraded_when_stale(
    client: TestClient,
    mapping_payload: dict[str, Any],
) -> None:
    engine: Engine = client.app.state.engine
    factory = client.app.state.session_factory
    mapping.ingest_payload(engine, mapping_payload)

    fresh = client.get("/api/system/doctor").json()
    fresh_check = {check["name"]: check for check in fresh["checks"]}["mapping"]
    assert fresh_check["status"] == "ok"

    with factory() as session:
        stale_time = (datetime.now(UTC) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        mapping.set_setting(session, mapping.SETTING_LAST_UPDATE, stale_time)

    stale = client.get("/api/system/doctor").json()
    stale_check = {check["name"]: check for check in stale["checks"]}["mapping"]
    assert stale_check["status"] == "degraded"
    assert "stale" in stale_check["detail"]
