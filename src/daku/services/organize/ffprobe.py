"""Media inspection with ``ffprobe``.

Only reads the file. ``ffprobe`` is never asked to write, remux or transcode, and
the source is never opened for writing by Daku.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daku.errors import AppError

PROBE_TIMEOUT_SECONDS = 30.0
MAX_PARALLEL_PROBES = 4
MISSING_FFPROBE_MESSAGE = (
    "ffprobe is not installed. Install ffmpeg (sudo apt install ffmpeg) and retry; "
    "downloads cannot be organised until media inspection works."
)


@dataclass(slots=True)
class MediaInfo:
    """The subset of ffprobe output Daku cares about."""

    container: str | None = None
    vcodec: str | None = None
    acodec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return self.vcodec is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "container": self.container,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "width": self.width,
            "height": self.height,
            "duration_s": self.duration_s,
            "audio_languages": list(self.audio_languages),
            "subtitle_languages": list(self.subtitle_languages),
        }


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def require_ffprobe() -> str:
    binary = find_ffprobe()
    if binary is None:
        raise AppError(
            code="FFPROBE_MISSING",
            message=MISSING_FFPROBE_MESSAGE,
            status_code=503,
        )
    return binary


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _language(stream: dict[str, Any]) -> str | None:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return None
    raw = tags.get("language")
    if not isinstance(raw, str) or not raw.strip() or raw.lower() == "und":
        return None
    return raw.strip().lower()


def _dedupe(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition")
        if isinstance(disposition, dict) and int(disposition.get("attached_pic") or 0) == 1:
            continue
        return stream
    return None


def parse_probe(data: dict[str, Any]) -> MediaInfo:
    """Parse ``ffprobe -print_format json`` output. Missing data is not an error."""

    streams_raw = data.get("streams")
    streams = (
        [stream for stream in streams_raw if isinstance(stream, dict)]
        if isinstance(streams_raw, list)
        else []
    )
    fmt_raw = data.get("format")
    fmt: dict[str, Any] = fmt_raw if isinstance(fmt_raw, dict) else {}

    video = _video_stream(streams)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    container_raw = fmt.get("format_name")
    container = (
        str(container_raw).split(",")[0].strip() or None if isinstance(container_raw, str) else None
    )

    duration = _as_float(fmt.get("duration"))
    if duration is None and video is not None:
        duration = _as_float(video.get("duration"))

    return MediaInfo(
        container=container,
        vcodec=str(video["codec_name"]) if video and video.get("codec_name") else None,
        acodec=(
            str(audio_streams[0]["codec_name"])
            if audio_streams and audio_streams[0].get("codec_name")
            else None
        ),
        width=_as_int(video.get("width")) if video is not None else None,
        height=_as_int(video.get("height")) if video is not None else None,
        duration_s=duration,
        audio_languages=_dedupe([_language(stream) for stream in audio_streams]),
        subtitle_languages=_dedupe([_language(stream) for stream in subtitle_streams]),
    )


def _probe_error(path: Path, detail: str) -> AppError:
    return AppError(
        code="MEDIA_PROBE_FAILED",
        message=f"Could not inspect {path.name}: {detail}",
        status_code=422,
        detail={"file": str(path)},
    )


def probe_sync(
    path: Path,
    *,
    ffprobe_bin: str | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> MediaInfo:
    """Run ffprobe synchronously. Call through :func:`probe` in async code."""

    path = Path(path)
    if not path.exists() or not path.is_file():
        raise AppError(
            code="MEDIA_FILE_MISSING",
            message=f"Media file {path.name} does not exist",
            status_code=422,
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _probe_error(path, str(exc)) from exc
    if size == 0:
        raise AppError(
            code="MEDIA_FILE_EMPTY",
            message=f"{path.name} is zero bytes; nothing can be inspected",
            status_code=422,
        )

    binary = ffprobe_bin or require_ffprobe()
    command = [
        binary,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AppError(
            code="FFPROBE_MISSING",
            message=MISSING_FFPROBE_MESSAGE,
            status_code=503,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _probe_error(path, f"ffprobe timed out after {timeout:.0f}s") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or "ffprobe exited with an error").strip().splitlines()
        raise _probe_error(path, detail[-1] if detail else "ffprobe exited with an error")

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise _probe_error(path, "ffprobe returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise _probe_error(path, "ffprobe returned an unexpected payload")
    return parse_probe(payload)


async def probe(
    path: Path,
    *,
    ffprobe_bin: str | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> MediaInfo:
    """Probe a file without blocking the event loop."""

    return await asyncio.to_thread(probe_sync, Path(path), ffprobe_bin=ffprobe_bin, timeout=timeout)
