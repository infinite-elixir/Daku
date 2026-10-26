"""Choose the primary video file in a completed download.

A torrent may contain a movie, a season pack, samples, trailers and subtitle
sidecars. This module scans the download directory, probes every video with
``ffprobe`` (at most four at a time), ranks the results and persists one
``media_files`` row per valid video. Exactly one may be primary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from daku.config import Settings
from daku.errors import AppError
from daku.models import Download, MediaFile, Release
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.services.organize.ffprobe import MediaInfo, probe

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts", ".m2ts", ".wmv", ".flv"}
)
SUBTITLE_EXTENSIONS: frozenset[str] = frozenset({".srt", ".ass", ".vtt"})
PRIMARY_MIN_BYTES = 50 * 1024 * 1024
MAX_PARALLEL_PROBES = 4

SAMPLE_DIRS: frozenset[str] = frozenset(
    {"sample", "samples", "extras", "extra", "featurettes", "trailers", "trailer"}
)
SAMPLE_TOKENS: tuple[str, ...] = ("sample", "trailer", "featurette", "preview")

ProbeFn = Callable[..., Awaitable[MediaInfo]]


@dataclass(slots=True)
class Candidate:
    path: Path
    size_bytes: int
    info: MediaInfo | None = None
    is_sample: bool = False
    error: str | None = None
    sidecars: list[Path] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return self.info is not None and self.info.has_video


@dataclass(slots=True)
class InspectionResult:
    download_id: str
    files: list[MediaFile]
    primary: MediaFile | None
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_id": self.download_id,
            "media_files": [_file_payload(row) for row in self.files],
            "primary_id": self.primary.id if self.primary is not None else None,
            "errors": list(self.errors),
        }


def _file_payload(row: MediaFile) -> dict[str, Any]:
    return {
        "id": row.id,
        "download_id": row.download_id,
        "title_id": row.title_id,
        "episode_id": row.episode_id,
        "src_path": row.src_path,
        "size_bytes": row.size_bytes,
        "container": row.container,
        "vcodec": row.vcodec,
        "acodec": row.acodec,
        "width": row.width,
        "height": row.height,
        "duration_s": row.duration_s,
        "is_primary": row.is_primary,
        "sidecars": media_repo.sidecars(row),
    }


def scan_video_files(root: Path) -> list[Path]:
    """Return every regular video file below ``root`` (never follows symlinks)."""

    root = Path(root)
    if root.is_file():
        return [root] if root.suffix.lower() in VIDEO_EXTENSIONS else []
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(path)
    return sorted(files)


def is_sample_path(path: Path, root: Path) -> bool:
    """Path heuristics only; size is applied separately."""

    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    directories = {part.lower() for part in parts[:-1]}
    if directories & SAMPLE_DIRS:
        return True
    stem = path.stem.lower()
    tokens = stem.replace("_", ".").replace("-", ".").replace(" ", ".").split(".")
    return any(token in SAMPLE_TOKENS for token in tokens if token)


def detect_sidecars(video: Path) -> list[Path]:
    """Subtitle files sharing the video's basename, case-insensitively."""

    folder = video.parent
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    stem = video.stem.casefold()
    sidecars = [
        entry
        for entry in entries
        if entry.is_file()
        and entry.suffix.lower() in SUBTITLE_EXTENSIONS
        and entry.stem.casefold() == stem
    ]
    return sorted(sidecars)


def build_candidate(path: Path, root: Path) -> Candidate:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return Candidate(
        path=path,
        size_bytes=size,
        is_sample=is_sample_path(path, root) or size < PRIMARY_MIN_BYTES,
        sidecars=detect_sidecars(path),
    )


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Rank valid videos by stream, non-sample status and size.

    Samples and trailers always sort below real content, even when they are
    large. Files without a video stream never outrank a playable file.
    """

    return sorted(
        candidates,
        key=lambda candidate: (
            1 if candidate.has_video else 0,
            0 if candidate.is_sample else 1,
            candidate.size_bytes,
        ),
        reverse=True,
    )


def select_primary(candidates: list[Candidate]) -> Candidate | None:
    for candidate in rank_candidates(candidates):
        if candidate.has_video and not candidate.is_sample:
            return candidate
    return None


async def _probe_candidates(
    candidates: list[Candidate],
    *,
    ffprobe_bin: str | None,
    probe_fn: ProbeFn,
    max_parallel: int = MAX_PARALLEL_PROBES,
) -> list[dict[str, str]]:
    semaphore = asyncio.Semaphore(max(1, max_parallel))
    errors: list[dict[str, str]] = []

    async def run(candidate: Candidate) -> None:
        async with semaphore:
            try:
                candidate.info = await probe_fn(candidate.path, ffprobe_bin=ffprobe_bin)
            except AppError as exc:
                candidate.error = exc.message
                errors.append(
                    {"file": str(candidate.path), "code": exc.code, "message": exc.message}
                )
            except Exception as exc:  # pragma: no cover - defensive
                candidate.error = str(exc)
                errors.append(
                    {"file": str(candidate.path), "code": "MEDIA_PROBE_FAILED", "message": str(exc)}
                )

    await asyncio.gather(*(run(candidate) for candidate in candidates))
    return errors


def _download_root(download: Download, settings: Settings | None, root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    if download.save_path:
        return Path(download.save_path)
    if settings is not None:
        return Path(settings.downloads_dir)
    raise AppError(
        code="DOWNLOAD_PATH_MISSING",
        message="This download has no save path to inspect",
        status_code=422,
    )


def _linked_release(session: Session, download: Download) -> Release:
    release = releases_repo.get_release(session, download.release_id or "")
    if release is None:
        raise AppError(
            code="DOWNLOAD_NOT_LINKED",
            message="This download is not linked to a library title",
            status_code=422,
        )
    return release


async def inspect_download(
    session: Session,
    download: Download,
    *,
    settings: Settings | None = None,
    root: Path | None = None,
    ffprobe_bin: str | None = None,
    probe_fn: ProbeFn | None = None,
    max_parallel: int = MAX_PARALLEL_PROBES,
) -> InspectionResult:
    """Scan, probe and persist the media files for a completed download."""

    release = _linked_release(session, download)
    directory = _download_root(download, settings, root)
    if not directory.exists():
        raise AppError(
            code="DOWNLOAD_PATH_MISSING",
            message=f"Download directory {directory.name} does not exist",
            status_code=422,
        )

    paths = scan_video_files(directory)
    if not paths:
        raise AppError(
            code="MEDIA_NOT_FOUND",
            message="No video files were found in this download",
            status_code=422,
        )

    candidates = [
        build_candidate(path, directory if directory.is_dir() else directory.parent)
        for path in paths
    ]
    errors = await _probe_candidates(
        candidates,
        ffprobe_bin=ffprobe_bin,
        probe_fn=probe_fn or probe,
        max_parallel=max_parallel,
    )

    valid = [candidate for candidate in candidates if candidate.has_video]
    if not valid:
        detail = errors[0]["message"] if errors else "no playable video stream"
        raise AppError(
            code="MEDIA_INSPECTION_FAILED",
            message=f"No playable video file could be inspected: {detail}",
            status_code=422,
            detail={"files": errors},
        )

    primary = select_primary(valid)
    media_repo.delete_by_download(session, download.id)

    rows: list[MediaFile] = []
    for candidate in valid:
        info = candidate.info or MediaInfo()
        rows.append(
            media_repo.create_media_file(
                session,
                title_id=release.title_id,
                download_id=download.id,
                src_path=str(candidate.path),
                size_bytes=candidate.size_bytes,
                container=info.container,
                vcodec=info.vcodec,
                acodec=info.acodec,
                width=info.width,
                height=info.height,
                duration_s=info.duration_s,
                is_primary=primary is not None and candidate.path == primary.path,
                sidecars=[str(path) for path in candidate.sidecars],
            )
        )

    primary_row = next((row for row in rows if row.is_primary), None)
    if primary_row is None:
        logger.info(
            "no primary media file selected",
            extra={"download_id": download.id, "reason": "all candidates are samples/too small"},
        )
    return InspectionResult(
        download_id=download.id,
        files=rows,
        primary=primary_row,
        errors=errors,
    )


def sidecars_json(paths: list[Path]) -> str | None:
    return json.dumps([str(path) for path in paths], ensure_ascii=False) if paths else None
