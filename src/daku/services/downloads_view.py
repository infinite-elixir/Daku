"""Shared download serialisation for the REST API and SSE payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from daku.config import Settings
from daku.models import Download, Episode, MediaFile, Title
from daku.repositories import media_files as media_files_repo
from daku.repositories import releases as releases_repo
from daku.services.safety import payload_deletion_safety


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


def _downloaded_files(
    session: Session,
    row: Download,
    release: Any,
) -> list[dict[str, Any]]:
    """Every file this torrent has produced, including earlier selections.

    A download row is keyed by info-hash and may be reused for another episode of
    the same pack (T-042). Rows written before that change detached the previous
    episode's media row instead of keeping it; those orphans are picked up again
    when they share a folder with a file this download still owns.
    """

    attached = media_files_repo.list_by_download(session, row.id)
    parents = {Path(item.src_path).parent for item in attached if item.src_path}
    rows: list[MediaFile] = list(attached)
    if release is not None and parents:
        for candidate in media_files_repo.list_by_title(session, release.title_id):
            if (
                candidate.download_id is None
                and candidate.src_path
                and Path(candidate.src_path).parent in parents
            ):
                rows.append(candidate)

    seen: set[str] = set()
    payload: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for item in rows:
        key = item.src_path.casefold()
        if key in seen:
            continue
        seen.add(key)
        episode = session.get(Episode, item.episode_id) if item.episode_id else None
        name = Path(item.src_path).name
        entry = {
            "id": item.id,
            "episode_id": item.episode_id,
            "episode_label": _label(episode) if episode is not None else None,
            "file_name": name,
            "size_bytes": item.size_bytes,
            "is_primary": item.is_primary,
            "present": media_files_repo.is_present(item),
        }
        if episode is None:
            order = (9999, 9999, name.casefold())
        else:
            order = (episode.season, episode.episode, name.casefold())
        payload.append((order, entry))

    return [entry for _order, entry in sorted(payload, key=lambda pair: pair[0])]


def download_payload(
    session: Session, row: Download, *, settings: Settings | None = None
) -> dict[str, Any]:
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
    if settings is None:
        can_delete_source_files = False
        delete_files_reason: str | None = "no_library_copy"
    else:
        can_delete_source_files, delete_files_reason = payload_deletion_safety(
            session, row, settings
        )
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
        "can_delete_source_files": can_delete_source_files,
        "delete_files_reason": delete_files_reason,
        "downloaded_files": _downloaded_files(session, row, release),
    }
