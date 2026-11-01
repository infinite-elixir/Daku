"""Choose the primary video file in a completed download.

A torrent may contain a movie, a season pack, samples, trailers and subtitle
sidecars. This module scans the download directory, probes every video with
``ffprobe`` (at most four at a time), ranks the results and persists one
``media_files`` row per valid video. Exactly one may be primary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from daku.config import Settings
from daku.errors import AppError
from daku.models import Download, MediaFile, Release, Title
from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.services.organize.ffprobe import MediaInfo
from daku.services.organize.ffprobe import probe as _default_probe
from daku.services.releases.episode_match import normalize_path, season_episode_from_path

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

# Kept as a module attribute so tests and callers can monkeypatch the default probe.
probe = _default_probe


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
        "present": media_repo.is_present(row),
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


def _selection(download: Download) -> tuple[list[int] | None, list[str]]:
    """Return the persisted selection as ``(indices, paths)``.

    Paths are authoritative for filtering because they were recorded from the
    engine or the probe. Indices remain a fallback for older/advanced rows.
    """

    indices: list[int] | None = None
    paths: list[str] = []
    if download.selected_files_json:
        try:
            raw = json.loads(download.selected_files_json)
        except (TypeError, ValueError):
            raw = None
        if isinstance(raw, list) and all(isinstance(item, int) for item in raw):
            indices = raw
    if download.file_match_json:
        try:
            match = json.loads(download.file_match_json)
        except (TypeError, ValueError):
            match = None
        if isinstance(match, dict):
            raw_paths = match.get("file_paths")
            if isinstance(raw_paths, list):
                paths = [item for item in raw_paths if isinstance(item, str)]
            if not paths and isinstance(match.get("path"), str):
                paths = [match["path"]]
    return indices, paths


def _path_keys(path: Path, root: Path) -> set[str]:
    keys = {normalize_path(str(path)), normalize_path(path.name)}
    with contextlib.suppress(ValueError):
        keys.add(normalize_path(str(path.relative_to(root))))
    return {key for key in keys if key}


def _filter_selected(paths: list[Path], root: Path, selected_paths: list[str]) -> list[Path]:
    if not selected_paths:
        return paths
    wanted = {normalize_path(item) for item in selected_paths if item}
    return [path for path in paths if _path_keys(path, root) & wanted]


async def _resolved_selected_paths(
    download: Download,
    release: Release,
    *,
    engine: Any | None,
    probe: Any | None,
) -> list[str]:
    """Resolve persisted indices to paths without ever trusting scan order."""

    _indices, paths = _selection(download)
    if paths:
        return paths
    indices = _indices
    if indices is None:
        return []

    by_index: dict[int, str] = {}
    if engine is not None:
        try:
            files = await engine.files(download.external_id)
        except Exception:
            files = []
        by_index = {item.index: item.path for item in files if getattr(item, "path", None)}
    if not by_index and probe is not None and release.magnet:
        try:
            metadata = await probe.probe(release.magnet)
        except Exception:
            metadata = None
        if metadata is not None:
            by_index = {item.index: item.path for item in metadata.files}

    resolved = [by_index[index] for index in dict.fromkeys(indices) if index in by_index]
    if len(resolved) != len(set(indices)):
        raise AppError(
            code="MEDIA_SELECTION_UNRESOLVED",
            message=(
                "The selected torrent file could not be resolved to a path; "
                "refusing to guess from the scan order"
            ),
            status_code=422,
        )
    return resolved


def _episode_id_for(
    session: Session,
    release: Release,
    title: Title | None,
    path: Path,
    *,
    single: bool,
) -> str | None:
    if title is None or title.kind == "movie":
        return None
    pair = season_episode_from_path(str(path))
    if pair is not None:
        episode = episodes_repo.find_episode(
            session, title_id=title.id, season=pair[0], episode=pair[1]
        )
        if episode is not None:
            return episode.id
    if single and release.episode_id:
        return release.episode_id
    return None


async def inspect_download(
    session: Session,
    download: Download,
    *,
    settings: Settings | None = None,
    root: Path | None = None,
    ffprobe_bin: str | None = None,
    probe_fn: ProbeFn | None = None,
    max_parallel: int = MAX_PARALLEL_PROBES,
    engine: Any | None = None,
    probe: Any | None = None,
) -> InspectionResult:
    """Scan, probe and persist the media files for a completed download."""

    release = _linked_release(session, download)
    title = session.get(Title, release.title_id)
    directory = _download_root(download, settings, root)
    if not directory.exists():
        raise AppError(
            code="DOWNLOAD_PATH_MISSING",
            message=f"Download directory {directory.name} does not exist",
            status_code=422,
        )

    paths = scan_video_files(directory)
    scan_root = directory if directory.is_dir() else directory.parent
    selected_paths = await _resolved_selected_paths(
        download,
        release,
        engine=engine,
        probe=probe,
    )
    paths = _filter_selected(paths, scan_root, selected_paths)
    if not paths:
        raise AppError(
            code="MEDIA_NOT_FOUND",
            message="No selected video files were found in this download",
            status_code=422,
        )

    candidates = [build_candidate(path, scan_root) for path in paths]
    default_probe = globals().get("probe", _default_probe)
    errors = await _probe_candidates(
        candidates,
        ffprobe_bin=ffprobe_bin,
        probe_fn=probe_fn or default_probe,
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
    # Re-inspection is idempotent for the files being inspected now, but rows from
    # earlier episodes fetched by the same torrent must survive: one download row
    # accumulates every file it has produced (T-042).
    current_paths = {str(candidate.path) for candidate in valid}
    for existing in media_repo.list_by_download(session, download.id):
        if existing.src_path in current_paths:
            media_repo.delete_media_file(session, existing.id)

    rows: list[MediaFile] = []
    for candidate in valid:
        info = candidate.info or MediaInfo()
        rows.append(
            media_repo.create_media_file(
                session,
                title_id=release.title_id,
                episode_id=_episode_id_for(
                    session,
                    release,
                    title,
                    candidate.path,
                    single=len(valid) == 1,
                ),
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
