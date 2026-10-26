"""Shared download serialisation for the REST API and SSE payloads."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from daku.models import Download, Episode, Title
from daku.repositories import releases as releases_repo


def _label(row: Episode) -> str:
    return f"S{row.season:02d}E{row.episode:02d}"


def download_payload(session: Session, row: Download) -> dict[str, Any]:
    """Return the public shape of a download row.

    Only relative/identifying data is included: ``save_path`` is deliberately
    omitted because SSE payloads must not leak absolute host paths.
    """

    release = releases_repo.get_release(session, row.release_id) if row.release_id else None
    title = session.get(Title, release.title_id) if release is not None else None
    episode = (
        session.get(Episode, release.episode_id)
        if release is not None and release.episode_id
        else None
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
    }
