"""``releases.metadata_json`` snapshots.

The snapshot is written when a release is saved and never recomputed on read: the
organiser may rename the file later, so the original hints are the durable record.

``source`` means the video source (``BluRay``, ``WEB-DL``, ...). The origin of the row
(``torrentio`` or ``manual``) is stored alongside it as ``resolved_from``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

ResolvedFrom = Literal["torrentio", "nyaa", "manual"]

SNAPSHOT_KEYS: tuple[str, ...] = (
    "source",
    "video_codec",
    "audio_hint",
    "languages",
    "release_group",
    "edition",
    "category",
    "trusted",
    "remake",
)


def load_metadata(raw: str | None) -> dict[str, Any]:
    """Parse a stored snapshot. Malformed or missing JSON degrades to ``{}``."""

    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def metadata_snapshot(
    hints: Mapping[str, Any] | None,
    *,
    resolved_from: ResolvedFrom,
) -> str:
    """Build the durable snapshot, keeping only known hint keys."""

    source = hints or {}
    payload: dict[str, Any] = {key: source.get(key) for key in SNAPSHOT_KEYS}
    if source.get("source") == "nyaa":
        payload["source"] = "nyaa"
        for key in ("category", "trusted", "remake"):
            if key in source:
                payload[key] = source[key]
    payload["resolved_from"] = resolved_from
    return json.dumps(payload, ensure_ascii=False)


def resolved_from(row: Any) -> ResolvedFrom:
    """Origin of a release row: explicit metadata wins, then the provider name."""

    value = load_metadata(getattr(row, "metadata_json", None)).get("resolved_from")
    if value == "torrentio":
        return "torrentio"
    if value == "nyaa":
        return "nyaa"
    if value == "manual":
        return "manual"
    if getattr(row, "provider", None) == "nyaa":
        return "nyaa"
    return "manual" if getattr(row, "provider", None) == "manual" else "torrentio"
