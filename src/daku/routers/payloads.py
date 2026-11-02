"""Shared response shapes for media rows.

Both the title-files endpoint and Continue Watching return media files, so the payload
lives here rather than in either router.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from daku.models import Episode, MediaFile
from daku.repositories import media_files as media_files_repo


def playback_payload(row: MediaFile) -> dict[str, Any] | None:
    if row.playback_position_s is None:
        return None
    duration = row.playback_duration_s or row.duration_s
    return {
        "position_s": row.playback_position_s,
        "duration_s": duration,
        "updated_at": row.playback_updated_at,
    }


def media_file_payload(session: Session, row: MediaFile) -> dict[str, Any]:
    episode = session.get(Episode, row.episode_id) if row.episode_id else None
    path = media_files_repo.usable_path(row)
    return {
        "id": row.id,
        "download_id": row.download_id,
        "episode_id": row.episode_id,
        "episode_label": f"S{episode.season:02d}E{episode.episode:02d}" if episode else None,
        "episode_name": episode.name if episode else None,
        "season": episode.season if episode else None,
        "episode": episode.episode if episode else None,
        "file_name": path.name if path is not None else Path(row.src_path).name,
        "size_bytes": row.size_bytes,
        "is_primary": row.is_primary,
        "has_episode": episode is not None,
        "playback": playback_payload(row),
    }
