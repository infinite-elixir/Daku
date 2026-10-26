"""Small shared helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """Return a UUIDv4 string used as a primary key."""

    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """Naive UTC timestamp as ISO-8601 text. The API appends ``Z``."""

    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def utc_now_iso_micro() -> str:
    """Naive UTC timestamp with microseconds, used for event ordering."""

    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="microseconds")
