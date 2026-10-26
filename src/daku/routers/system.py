"""System router: doctor checks and mapping dataset maintenance."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from daku.config import Settings
from daku.db import get_session
from daku.errors import AppError
from daku.services.doctor import doctor_payload, probe_engines
from daku.services.metadata.mapping import MappingError, mapping_status, refresh

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/doctor")
async def doctor(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    supervisor = getattr(request.app.state, "aria2_supervisor", None)
    checks = await probe_engines(
        settings,
        {
            "qbittorrent": request.app.state.qbt_engine,
            "aria2": request.app.state.aria2_engine,
        },
        aria2_binary_available=supervisor.available if supervisor is not None else None,
    )
    probe = getattr(request.app.state, "probe", None)
    probe_status = probe.status().to_dict() if probe is not None else None
    return doctor_payload(
        settings, session, request.app.state.omdb, checks, probe_status=probe_status
    )


@router.get("/mappings")
def mappings(session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:
    return mapping_status(session).to_dict()


@router.post("/mappings/refresh")
async def refresh_mappings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.http_client
    status = mapping_status(session)
    # End the read transaction so SQLite's next read sees the ingest transaction.
    session.rollback()
    try:
        report = await refresh(
            request.app.state.engine,
            settings,
            client,
            url=status.url,
        )
    except MappingError as exc:
        raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
    payload = report.to_dict()
    payload["status"] = mapping_status(session).to_dict()
    return payload
