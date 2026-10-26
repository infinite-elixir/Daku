"""Activity event repository."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from daku.models import Event
from daku.utils import utc_now_iso_micro


def add_event(
    session: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    level: str = "info",
) -> Event:
    row = Event(
        ts=utc_now_iso_micro(),
        level=level,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_events(session: Session, *, limit: int = 100, after_id: int | None = None) -> list[Event]:
    statement = select(Event)
    if after_id is not None:
        statement = statement.where(Event.id > after_id)
    statement = statement.order_by(Event.id.desc()).limit(limit)
    return list(session.scalars(statement))


def latest_event_id(session: Session) -> int:
    value = session.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
    return int(value or 0)
