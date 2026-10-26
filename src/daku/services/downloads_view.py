"""Shared download serialisation for the REST API and SSE payloads."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from daku.models import Download, Episode, Title
from daku.repositories import releases as releases_repo


def _label(row: Episode) -> str:
    return f"S{row.season:02d}E{row.episode:02d}"


def _json_value(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _selection_label(
    selected: list[int] | None,
    match: dict[str, Any] | None,
    episode: Episode | None,
) -> str | None:
    if selected is None:
        if match and match.get("pack"):
            return "Whole pack"
        return None
    if not selected:
        return None
    if len(selected) == 1 and episode is not None:
        return f"{_label(episode)} only"
    if len(selected) == 1:
        return "1 file only"
    total = match.get("total_files") if isinstance(match, dict) else None
    first = selected[0] + 1
    if isinstance(total, int) and total > 0:
        return f"File {first} of {total}"
    return f"{len(selected)} files"


def download_payload(session: Session, row: Download) -> dict[str, Any]:
    """Return the public shape of a download row.

    Only relative/identifying data is included: ``save_path`` is deliberately
    omitted because SSE payloads must not leak absolute host paths.
    """

    release = releases_repo.get_release(session, row.release_id) if row.release_id else None
    title = session.get(Title, release.title_id) if release is not None else None
    selected = _json_value(row.selected_files_json, None)
    if not isinstance(selected, list) or not all(isinstance(item, int) for item in selected):
        selected = None
    match = _json_value(row.file_match_json, None)
    if not isinstance(match, dict):
        match = None
    episode_id = release.episode_id if release is not None else None
    if not episode_id and match is not None:
        # The user may have supplied the episode on the download request rather
        # than the release row; the matcher stores it in file_match_json.
        candidate = match.get("episode_id")
        episode_id = candidate if isinstance(candidate, str) else None
    episode = session.get(Episode, episode_id) if episode_id else None
    selected_size = match.get("selected_size_bytes") if isinstance(match, dict) else None
    if not isinstance(selected_size, int) or selected_size <= 0:
        selected_size = None
    return {
        "id": row.id,
        "release_id": row.release_id,
        "source": row.source,
        "engine": row.engine,
        "external_id": row.external_id,
        "status": row.status,
        "progress": row.progress,
        "downloaded_bytes": row.downloaded_bytes,
        "total_bytes": row.total_bytes,
        "speed_bps": row.speed_bps,
        "eta_seconds": row.eta_seconds,
        "error": row.error,
        "save_path": row.save_path,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "title_id": title.id if title is not None else None,
        "title": title.title if title is not None else None,
        "episode_id": episode.id if episode is not None else None,
        "episode_label": _label(episode) if episode is not None else None,
        "quality": release.quality if release is not None else None,
        "filename": release.filename if release is not None else None,
        "selected_files": selected,
        "file_match": match,
        "selection_label": _selection_label(selected, match, episode),
        "selection_size_bytes": selected_size,
    }
