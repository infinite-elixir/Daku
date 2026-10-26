"""Turn a release, an episode and an optional file choice into download metadata.

This module is the bridge between the pure matcher and the HTTP/worker layers. It
probes the release magnet (T-030 cache), decides what the user meant and returns
the engine indices plus the JSON payloads stored on the download row. It never
creates a download row itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from daku.errors import AppError
from daku.models import Episode, Release, Title
from daku.repositories import episodes as episodes_repo
from daku.services.releases.episode_match import EpisodeTarget, match_episode, normalize_path
from daku.services.torrents.probe import ProbeError, ProbeService, TorrentFile, TorrentMetadata


@dataclass(slots=True)
class Selection:
    indices: list[int] | None = None
    paths: list[str] | None = None
    match: dict[str, Any] | None = None
    metadata: TorrentMetadata | None = None

    @property
    def selected_files_json(self) -> str | None:
        if self.indices is None:
            return None
        return json.dumps(self.indices)

    @property
    def file_match_json(self) -> str | None:
        if not self.match:
            return None
        return json.dumps(self.match, ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "selected_files": self.indices,
            "match": self.match,
        }
        if self.paths:
            payload["selected_paths"] = self.paths
        if self.metadata is not None:
            payload["files"] = [item.to_dict() for item in self.metadata.files]
            payload["total_files"] = len(self.metadata.files)
        return payload


@dataclass(slots=True)
class ResolveRequest:
    episode_id: str | None = None
    file_indices: list[int] | None = None
    file_paths: list[str] | None = None
    allow_pack: bool = False


def _episode_label(episode: Episode) -> str:
    return f"S{episode.season:02d}E{episode.episode:02d}"


def _target(episode: Episode) -> EpisodeTarget:
    return EpisodeTarget(
        season=episode.season,
        episode=episode.episode,
        absolute=episode.absolute,
        name=episode.name,
    )


def _metadata_files(metadata: TorrentMetadata) -> list[dict[str, Any]]:
    return [item.to_dict() for item in metadata.files]


def _resolve_path(path: str, files: list[TorrentFile]) -> TorrentFile | None:
    wanted = normalize_path(path)
    if not wanted:
        return None
    exact = [item for item in files if normalize_path(item.path) == wanted]
    if len(exact) == 1:
        return exact[0]
    return None


def _episode_for(
    session: Session,
    release: Release,
    requested_episode_id: str | None,
) -> Episode | None:
    episode_id = requested_episode_id or release.episode_id
    if not episode_id:
        return None
    episode = episodes_repo.get_episode(session, episode_id)
    if episode is None or episode.title_id != release.title_id:
        raise AppError(
            code="EPISODE_NOT_FOUND",
            message=f"Episode {episode_id} is not part of this title",
            status_code=404,
        )
    return episode


async def _probe(probe: ProbeService | None, release: Release) -> TorrentMetadata | None:
    if probe is None or not release.magnet:
        return None
    try:
        return await probe.probe(release.magnet)
    except ProbeError:
        return None


async def _probe_required(probe: ProbeService | None, release: Release) -> TorrentMetadata:
    if probe is None:
        raise AppError(
            code="PROBE_UNAVAILABLE",
            message="File selection needs magnet probing (install daku[probe])",
            status_code=503,
        )
    if not release.magnet:
        raise AppError(
            code="RELEASE_NOT_DOWNLOADABLE",
            message="This release has no magnet to inspect",
            status_code=422,
        )
    try:
        return await probe.probe(release.magnet)
    except ProbeError as exc:
        status = 503 if exc.code == "PROBE_UNAVAILABLE" else 504
        if exc.code == "PROBE_BUSY":
            status = 429
        raise AppError(code=exc.code, message=exc.message, status_code=status) from exc


async def resolve_selection(
    session: Session,
    release: Release,
    *,
    probe: ProbeService | None,
    request: ResolveRequest | None = None,
) -> Selection:
    """Return what this download should fetch, or raise a stable 409."""

    request = request or ResolveRequest()
    title = session.get(Title, release.title_id)
    if title is None:
        raise AppError(
            code="TITLE_NOT_FOUND",
            message="This release is not linked to a library title",
            status_code=404,
        )
    if title.kind == "movie":
        return Selection()

    episode = _episode_for(session, release, request.episode_id)

    # Explicit paths are mapped against the probe list. They win over every
    # matcher rule and are validated before a download row exists.
    if request.file_paths:
        metadata = await _probe_required(probe, release)
        chosen: list[TorrentFile] = []
        for path in request.file_paths:
            found = _resolve_path(path, metadata.files)
            if found is None:
                raise AppError(
                    code="FILE_NOT_IN_TORRENT",
                    message=f"The torrent does not contain {path}",
                    status_code=409,
                    detail={
                        "path": path,
                        "files": _metadata_files(metadata),
                    },
                )
            if found not in chosen:
                chosen.append(found)
        indices = [item.index for item in chosen]
        paths = [item.path for item in chosen]
        return Selection(
            indices=indices,
            paths=paths,
            match={
                "source": "explicit",
                "episode_id": episode.id if episode is not None else None,
                "file_index": indices[0] if len(indices) == 1 else None,
                "confidence": 100,
                "reason": "explicit selection",
                "file_paths": paths,
                "total_files": len(metadata.files),
            },
            metadata=metadata,
        )

    if request.file_indices:
        indices = list(dict.fromkeys(request.file_indices))
        return Selection(
            indices=indices,
            match={
                "source": "explicit",
                "episode_id": episode.id if episode is not None else None,
                "file_index": indices[0] if len(indices) == 1 else None,
                "confidence": 100,
                "reason": "explicit selection",
                "file_indices": indices,
            },
        )

    probed = await _probe(probe, release)
    if probed is None:
        # Probing is optional. A single-file release can still be downloaded the
        # old way; a multi-file torrent cannot be guessed without the probe.
        return Selection(
            match={
                "episode_id": episode.id if episode is not None else None,
                "reason": "magnet probe unavailable; using the engine default",
            }
        )

    files = probed.files
    if len(files) <= 1:
        # A single-file release keeps the old "download the release" behaviour, but
        # still records an episode match when the file name identifies one. A miss
        # is never fatal here: there is nothing else in the torrent to choose.
        matched = (
            match_episode(
                files,
                _target(episode),
                title_kind=title.kind,
                release_filename=release.filename,
                title_name=title.title,
            ).match
            if episode is not None and files
            else None
        )
        if matched is not None:
            return Selection(
                indices=[matched.index],
                paths=[matched.path],
                match={
                    "episode_id": episode.id if episode is not None else None,
                    "file_index": matched.index,
                    "confidence": matched.score,
                    "reason": matched.reason,
                    "file_paths": [matched.path],
                    "total_files": len(files),
                },
                metadata=probed,
            )
        return Selection(
            match={
                "episode_id": episode.id if episode is not None else None,
                "reason": "single-file release",
                "total_files": len(files),
            },
            metadata=probed,
        )

    if episode is None:
        if request.allow_pack:
            return Selection(
                match={
                    "pack": True,
                    "reason": "whole pack requested",
                    "total_files": len(files),
                },
                metadata=probed,
            )
        raise AppError(
            code="PACK_SELECTION_REQUIRED",
            message=("This release contains multiple files. Choose one or confirm the whole pack."),
            status_code=409,
            detail={
                "files": _metadata_files(probed),
                "total_files": len(files),
                "hint": "Send file_paths, file_indices or allow_pack=true",
            },
        )

    outcome = match_episode(
        files,
        _target(episode),
        title_kind=title.kind,
        release_filename=release.filename,
        title_name=title.title,
    )
    if outcome.match is not None:
        winner = outcome.match
        paths = [winner.path]
        return Selection(
            indices=[winner.index],
            paths=paths,
            match={
                "episode_id": episode.id,
                "file_index": winner.index,
                "confidence": winner.score,
                "reason": winner.reason,
                "file_paths": paths,
                "total_files": len(files),
            },
            metadata=probed,
        )
    if outcome.ambiguous:
        raise AppError(
            code="FILE_SELECTION_REQUIRED",
            message=(
                f"More than one file in this release matches {_episode_label(episode)}. "
                "Choose one, or download the whole pack."
            ),
            status_code=409,
            detail={
                "episode_id": episode.id,
                "episode_label": _episode_label(episode),
                "candidates": [item.to_dict() for item in outcome.candidates],
                "files": _metadata_files(probed),
                "hint": "Send file_paths with one candidate, or allow_pack=true",
            },
        )
    raise AppError(
        code="EPISODE_FILE_NOT_FOUND",
        message=(
            f"No file in this release could be identified as {_episode_label(episode)}. "
            "Choose a file manually or download the whole pack."
        ),
        status_code=409,
        detail={
            "episode_id": episode.id,
            "episode_label": _episode_label(episode),
            "files": _metadata_files(probed),
            "candidates": [],
            "hint": "Send file_paths from the file list, or allow_pack=true",
        },
    )


__all__ = ["ResolveRequest", "Selection", "resolve_selection"]
