"""ffmpeg-backed thumbnails for files Daku has already filed.

Cache files are named by media-file id, never by path: the path can move, the row id
cannot. Nothing here runs during a library read; extraction only happens when a card
asks for its image.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from daku.config import Settings
from daku.errors import AppError
from daku.models import MediaFile
from daku.repositories import media_files as media_files_repo

logger = logging.getLogger(__name__)

THUMBNAIL_UNAVAILABLE = "THUMBNAIL_UNAVAILABLE"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_CONCURRENT = 2
DEFAULT_FAILURE_TTL_SECONDS = 300.0
THUMBNAIL_WIDTH = 960


@dataclass(frozen=True, slots=True)
class ThumbnailResult:
    path: Path
    etag: str


Runner = Callable[..., Awaitable[Any]]
Which = Callable[[str], str | None]


def _unavailable(message: str, *, status_code: int = 404) -> AppError:
    return AppError(code=THUMBNAIL_UNAVAILABLE, message=message, status_code=status_code)


def _seek_seconds(duration_s: float | None) -> float:
    if duration_s is not None and duration_s > 0:
        seek = duration_s * 0.25
        # Keep the frame inside the file even when it is very short.
        return max(0.0, min(seek, max(0.0, duration_s - 1.0)))
    return 60.0


class ThumbnailService:
    """Serialise extraction, cache successes on disk and failures in memory."""

    def __init__(
        self,
        settings: Settings,
        *,
        ffmpeg_bin: str = "ffmpeg",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        failure_ttl: float = DEFAULT_FAILURE_TTL_SECONDS,
        which: Which | None = None,
        runner: Runner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.failure_ttl = failure_ttl
        self._which = which or shutil.which
        self._runner = runner or asyncio.create_subprocess_exec
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._failures: dict[str, tuple[float, int]] = {}
        self._missing_logged = False

    @property
    def cache_dir(self) -> Path:
        return self.settings.data_dir / "thumbnails"

    def cache_path(self, media_file_id: str) -> Path:
        return self.cache_dir / f"{media_file_id}.jpg"

    def _source_path(self, row: MediaFile) -> tuple[Path, os.stat_result]:
        path = media_files_repo.usable_path(row)
        if path is None:
            raise _unavailable("This media file has no recorded path")
        try:
            info = path.stat()
        except (OSError, ValueError) as exc:
            raise _unavailable("This media file is no longer on disk") from exc
        if not path.is_file():
            raise _unavailable("This media file is no longer on disk")
        return path, info

    def etag(self, row: MediaFile) -> str:
        _path, info = self._source_path(row)
        return f"{row.id}:{info.st_size}:{int(info.st_mtime)}"

    def _recent_failure(self, media_file_id: str) -> AppError | None:
        cached = self._failures.get(media_file_id)
        if cached is None:
            return None
        until, status_code = cached
        if self._clock() >= until:
            self._failures.pop(media_file_id, None)
            return None
        message = (
            "ffmpeg is not installed; thumbnails are unavailable"
            if status_code == 503
            else "A thumbnail could not be generated for this file"
        )
        return _unavailable(message, status_code=status_code)

    def _remember_failure(self, media_file_id: str, status_code: int) -> None:
        self._failures[media_file_id] = (
            self._clock() + self.failure_ttl,
            status_code,
        )

    async def get(self, row: MediaFile) -> ThumbnailResult:
        source, info = self._source_path(row)
        etag = f"{row.id}:{info.st_size}:{int(info.st_mtime)}"
        target = self.cache_path(row.id)
        if target.is_file():
            return ThumbnailResult(path=target, etag=etag)

        failed = self._recent_failure(row.id)
        if failed is not None:
            raise failed

        binary = self._which(self.ffmpeg_bin)
        if not binary:
            if not self._missing_logged:
                logger.error("ffmpeg not found; media thumbnails are unavailable")
                self._missing_logged = True
            self._remember_failure(row.id, 503)
            raise _unavailable(
                "ffmpeg is not installed; thumbnails are unavailable", status_code=503
            )

        async with self._semaphore:
            # A second request may have filled the cache while this one waited.
            if target.is_file():
                return ThumbnailResult(path=target, etag=etag)
            failed = self._recent_failure(row.id)
            if failed is not None:
                raise failed

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{row.id}.{uuid4().hex}.tmp.jpg")
            argv = [
                binary,
                "-y",
                "-ss",
                f"{_seek_seconds(row.duration_s):.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={THUMBNAIL_WIDTH}:-2",
                "-q:v",
                "4",
                str(temporary),
            ]
            try:
                process = await self._runner(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(process.communicate(), timeout=self.timeout)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    logger.warning(
                        "thumbnail extraction timed out",
                        extra={"media_file_id": row.id, "exit_code": None},
                    )
                    self._remember_failure(row.id, 404)
                    raise _unavailable("Thumbnail extraction timed out") from None

                return_code = process.returncode
                if return_code != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                    logger.warning(
                        "thumbnail extraction failed",
                        extra={"media_file_id": row.id, "exit_code": return_code},
                    )
                    self._remember_failure(row.id, 404)
                    raise _unavailable("A thumbnail could not be generated for this file")

                os.replace(temporary, target)
                logger.info(
                    "thumbnail extracted",
                    extra={"media_file_id": row.id, "exit_code": return_code},
                )
                return ThumbnailResult(path=target, etag=etag)
            finally:
                temporary.unlink(missing_ok=True)
