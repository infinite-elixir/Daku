"""qBittorrent Web API v2 adapter.

One adapter instance owns one cookie jar. Authentication is lazy, and any ``403``
re-authenticates once and retries the request, which is exactly what happens when
qBittorrent restarts and forgets its sessions.

Magnets are sent in the form body and never logged. The download row stores only the
release id and the info-hash; the magnet itself stays on the release row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import PurePosixPath

import httpx

from daku.config import Settings
from daku.services.engines.base import (
    AddRequest,
    EngineError,
    EngineFile,
    EngineStatus,
    Health,
)
from daku.services.releases.episode_match import normalize_path

QBT_TIMEOUT_SECONDS = 10.0
QBT_INFINITE_ETA = 8_640_000
QBT_CATEGORY = "daku"
QBT_METADATA_TIMEOUT_SECONDS = 30.0
QBT_METADATA_POLL_SECONDS = 0.5
QBT_PRIORITY_SKIP = 0
QBT_PRIORITY_NORMAL = 1

ACTIVE_STATES = frozenset(
    {
        "metaDL",
        "forcedMetaDL",
        "downloading",
        "stalledDL",
        "forcedDL",
        "checkingDL",
        "allocating",
        "checkingResumeData",
        "moving",
    }
)
QUEUED_STATES = frozenset({"queuedDL", "queuedUP", "checkingUP", "queuedForChecking"})
PAUSED_STATES = frozenset({"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"})
COMPLETE_STATES = frozenset({"uploading", "stalledUP", "forcedUP"})
FAILED_STATES = frozenset({"error", "missingFiles"})


def map_state(state: str, progress: float) -> str:
    """Map a qBittorrent torrent state onto the Daku state machine."""

    if state in FAILED_STATES:
        return "failed"
    # A seeding torrent is finished. qBittorrent keeps reporting it as "uploading",
    # which must never look like a download in progress.
    if progress >= 1.0:
        return "complete"
    if state in PAUSED_STATES:
        return "paused"
    if state in QUEUED_STATES:
        return "queued"
    if state in ACTIVE_STATES:
        return "active"
    if state in COMPLETE_STATES:
        return "complete"
    return "active"


def _login_accepted(response: httpx.Response) -> bool:
    """qBittorrent 4.x/5.0 answered ``200 Ok.``; 5.2+ answers ``204 No Content``."""

    if response.status_code == 204:
        return True
    return response.status_code == 200 and response.text.strip() == "Ok."


def _as_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


class QbittorrentEngine:
    """Drive one qBittorrent instance over its Web API."""

    name = "qbittorrent"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base = settings.qbt_url.rstrip("/")
        self._username = settings.qbt_user
        self._password = settings.qbt_pass
        self._client = client or httpx.AsyncClient(
            timeout=QBT_TIMEOUT_SECONDS, follow_redirects=True
        )
        self._owns_client = client is None
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self.metadata_timeout = QBT_METADATA_TIMEOUT_SECONDS
        self.metadata_poll_interval = QBT_METADATA_POLL_SECONDS

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- auth -----------------------------------------------------------------

    async def login(self) -> None:
        async with self._auth_lock:
            try:
                response = await self._client.post(
                    f"{self._base}/api/v2/auth/login",
                    data={"username": self._username, "password": self._password},
                    headers={"Referer": self._base},
                )
            except httpx.HTTPError as exc:
                self._authenticated = False
                raise EngineError("QBT_UNREACHABLE", f"qBittorrent is unreachable: {exc}") from exc
            if _login_accepted(response):
                self._authenticated = True
                return
            self._authenticated = False
            body = response.text.strip()
            if response.status_code == 401:
                raise EngineError(
                    "QBT_LOGIN_FAILED",
                    "qBittorrent rejected the username or password",
                )
            if response.status_code == 403:
                if "ban" in body.lower():
                    raise EngineError(
                        "QBT_LOGIN_FAILED",
                        "qBittorrent banned this IP after failed logins; wait a few minutes",
                    )
                raise EngineError(
                    "QBT_LOGIN_FAILED",
                    f"qBittorrent refused the login: {body[:200]}"
                    if body
                    else "qBittorrent refused the login",
                )
            if response.status_code != 200:
                raise EngineError(
                    "QBT_LOGIN_FAILED",
                    f"qBittorrent login returned HTTP {response.status_code}",
                )
            raise EngineError("QBT_LOGIN_FAILED", "qBittorrent rejected the login")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        **kwargs: object,
    ) -> httpx.Response:
        if authenticate and not self._authenticated:
            await self.login()

        async def _send() -> httpx.Response:
            return await self._client.request(
                method,
                f"{self._base}{path}",
                headers={"Referer": self._base},
                **kwargs,  # type: ignore[arg-type]
            )

        try:
            response = await _send()
            if response.status_code == 403 and authenticate:
                await self.login()
                response = await _send()
        except httpx.HTTPError as exc:
            self._authenticated = False
            raise EngineError("QBT_UNREACHABLE", f"qBittorrent is unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise EngineError(
                "QBT_UNREACHABLE", f"qBittorrent returned HTTP {response.status_code}"
            )
        if response.status_code == 403:
            self._authenticated = False
            raise EngineError("QBT_LOGIN_FAILED", "qBittorrent denied the request")
        return response

    # -- engine protocol ------------------------------------------------------

    async def add(self, request: AddRequest) -> str:
        response = await self._request(
            "POST",
            "/api/v2/torrents/add",
            files={
                "urls": (None, request.url),
                "savepath": (None, request.save_path),
                "category": (None, QBT_CATEGORY),
                "tags": (None, request.tag or ""),
                "sequentialDownload": (None, "false"),
                "firstLastPiecePrio": (None, "true"),
                "paused": (None, "true" if request.paused else "false"),
            },
        )
        body = response.text.strip()
        if body != "Ok.":
            raise EngineError(
                "QBT_REJECTED",
                "qBittorrent rejected the torrent" if body == "Fails." else body[:200],
            )
        return request.external_id.lower()

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        wanted = [item.lower() for item in external_ids if item]
        if not wanted:
            return {}
        response = await self._request(
            "GET",
            "/api/v2/torrents/info",
            params={"hashes": "|".join(wanted)},
        )
        try:
            rows = response.json()
        except ValueError as exc:
            raise EngineError("QBT_UNREACHABLE", "qBittorrent returned invalid JSON") from exc
        if not isinstance(rows, list):
            raise EngineError("QBT_UNREACHABLE", "qBittorrent returned an unexpected payload")

        statuses: dict[str, EngineStatus] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            info_hash = str(row.get("hash") or "").lower()
            if not info_hash:
                continue
            progress_value = row.get("progress")
            try:
                progress = float(progress_value) if progress_value is not None else 0.0
            except (TypeError, ValueError):
                progress = 0.0
            progress = min(max(progress, 0.0), 1.0)

            total = _as_int(row.get("size")) or _as_int(row.get("total_size"))
            downloaded = _as_int(row.get("completed"))
            if downloaded is None and total is not None:
                downloaded = round(progress * total)
            if progress <= 0.0 and downloaded and total:
                progress = min(downloaded / total, 1.0)

            eta = _as_int(row.get("eta"))
            if eta is not None and (eta < 0 or eta >= QBT_INFINITE_ETA):
                eta = None

            speed = _as_int(row.get("dlspeed"))
            state = str(row.get("state") or "")
            path = row.get("content_path") or row.get("save_path")
            files = [str(path)] if path else []
            statuses[info_hash] = EngineStatus(
                external_id=info_hash,
                status=map_state(state, progress),
                progress=progress,
                downloaded_bytes=downloaded or 0,
                total_bytes=total,
                speed_bps=speed if speed and speed > 0 else None,
                eta_seconds=eta,
                error=None,
                files=files,
            )
        return statuses

    async def pause(self, external_id: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/pause",
            data={"hashes": external_id.lower()},
        )

    async def resume(self, external_id: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/resume",
            data={"hashes": external_id.lower()},
        )

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={
                "hashes": external_id.lower(),
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    async def files(self, external_id: str) -> list[EngineFile]:
        """Return the engine's current file list, or an empty list before metadata."""

        response = await self._request(
            "GET",
            "/api/v2/torrents/files",
            params={"hash": external_id.lower()},
        )
        if response.status_code == 404:
            return []
        try:
            rows = response.json()
        except ValueError as exc:
            raise EngineError("QBT_UNREACHABLE", "qBittorrent returned invalid JSON") from exc
        if not isinstance(rows, list):
            raise EngineError("QBT_UNREACHABLE", "qBittorrent returned an unexpected payload")

        files: list[EngineFile] = []
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            try:
                index = int(row.get("index", position))
            except (TypeError, ValueError):
                index = position
            try:
                size = int(row.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            try:
                progress = float(row.get("progress") or 0.0)
            except (TypeError, ValueError):
                progress = 0.0
            try:
                priority = int(row.get("priority", QBT_PRIORITY_NORMAL))
            except (TypeError, ValueError):
                priority = QBT_PRIORITY_NORMAL
            files.append(
                EngineFile(
                    index=index,
                    path=str(row.get("name") or ""),
                    size_bytes=size,
                    progress=min(max(progress, 0.0), 1.0),
                    priority=priority,
                )
            )
        files.sort(key=lambda item: item.index)
        return files

    async def _wait_for_files(self, external_id: str) -> list[EngineFile]:
        """Wait until qBittorrent has parsed the torrent metadata."""

        deadline = asyncio.get_running_loop().time() + self.metadata_timeout
        while True:
            files = await self.files(external_id)
            if files:
                return files
            if asyncio.get_running_loop().time() >= deadline:
                raise EngineError(
                    "QBT_FILE_SELECTION_PENDING",
                    "qBittorrent did not return the torrent file list in time",
                )
            await asyncio.sleep(self.metadata_poll_interval)

    @staticmethod
    def _find_path(target: str, files: Sequence[EngineFile]) -> EngineFile | None:
        wanted = normalize_path(target)
        if not wanted:
            return None
        exact = [item for item in files if normalize_path(item.path) == wanted]
        if len(exact) == 1:
            return exact[0]
        wanted_name = normalize_path(PurePosixPath(target).name or target)
        named = [
            item
            for item in files
            if normalize_path(PurePosixPath(item.path).name or item.path) == wanted_name
        ]
        return named[0] if len(named) == 1 else None

    async def set_file_selection(
        self,
        external_id: str,
        indices: Sequence[int],
        *,
        paths: Sequence[str] | None = None,
    ) -> list[int]:
        """Set every unselected file to priority 0, selected files to normal, then resume."""

        files = await self._wait_for_files(external_id)

        if paths:
            selected: list[int] = []
            missing: list[str] = []
            for path in paths:
                found = self._find_path(path, files)
                if found is None:
                    missing.append(path)
                elif found.index not in selected:
                    selected.append(found.index)
            if missing:
                raise EngineError(
                    "QBT_FILE_NOT_IN_TORRENT",
                    f"The torrent does not contain {missing[0]}",
                )
        else:
            available = {item.index for item in files}
            selected = [index for index in dict.fromkeys(indices) if index in available]
            if len(selected) != len(set(indices)):
                raise EngineError(
                    "QBT_FILE_NOT_IN_TORRENT",
                    "A selected file index is not present in the torrent",
                )
        if not selected:
            raise EngineError(
                "QBT_FILE_NOT_IN_TORRENT",
                "No selected files are present in the torrent",
            )

        chosen = set(selected)
        ids = [str(item.index) for item in files]
        priorities = [
            str(QBT_PRIORITY_NORMAL if item.index in chosen else QBT_PRIORITY_SKIP)
            for item in files
        ]
        await self._request(
            "POST",
            "/api/v2/torrents/filePrio",
            data={
                "hash": external_id.lower(),
                "id": "|".join(ids),
                "priority": "|".join(priorities),
            },
        )
        await self.resume(external_id)
        return selected

    async def health(self) -> Health:
        try:
            await self.login()
            response = await self._request("GET", "/api/v2/app/version", authenticate=False)
        except EngineError as exc:
            return Health(ok=False, detail=exc.message)
        if response.status_code != 200:
            return Health(ok=False, detail=f"HTTP {response.status_code}")
        version = response.text.strip() or "unknown"
        return Health(ok=True, detail=f"qBittorrent {version}", version=version)
