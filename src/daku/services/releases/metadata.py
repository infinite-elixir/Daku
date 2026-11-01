"""``releases.metadata_json`` snapshots.

The snapshot is written when a release is saved and never recomputed on read: the
organiser may rename the file later, so the original hints are the durable record.

``source`` means the video source (``BluRay``, ``WEB-DL``, ...). The origin of the row
(``torrentio``, ``animetosho`` or ``manual``) is stored alongside it as ``resolved_from``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

ResolvedFrom = Literal["torrentio", "animetosho", "manual"]

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
    "status",
    "num_files",
    "published_at",
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
    payload["resolved_from"] = resolved_from
    return json.dumps(payload, ensure_ascii=False)


def resolved_from(row: Any) -> ResolvedFrom:
    """Origin of a release row: explicit metadata wins, then the provider name.

    ``nyaa`` is a legacy stored origin from before AnimeTosho replaced it; it maps to
    ``animetosho`` so old saved rows keep rendering without a data migration.
    """

    value = load_metadata(getattr(row, "metadata_json", None)).get("resolved_from")
    if value == "torrentio":
        return "torrentio"
    if value in {"animetosho", "nyaa"}:
        return "animetosho"
    if value == "manual":
        return "manual"
    if getattr(row, "provider", None) in {"animetosho", "nyaa"}:
        return "animetosho"
    return "manual" if getattr(row, "provider", None) == "manual" else "torrentio"
