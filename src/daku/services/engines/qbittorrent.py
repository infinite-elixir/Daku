"""qBittorrent Web API v2 adapter.

One adapter instance owns one cookie jar. Authentication is lazy, and any ``403``
re-authenticates once and retries the request, which is exactly what happens when
qBittorrent restarts and forgets its sessions.

Magnets are sent in the form body and never logged. The download row stores only the
release id and the info-hash; the magnet itself stays on the release row.
"""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)

QBT_TIMEOUT_SECONDS = 10.0
QBT_INFINITE_ETA = 8_640_000
QBT_CATEGORY = "daku"
QBT_METADATA_TIMEOUT_SECONDS = 30.0
QBT_METADATA_POLL_SECONDS = 0.5
QBT_PRIORITY_SKIP = 0
QBT_PRIORITY_NORMAL = 1
# qBittorrent bans an IP after five failed logins. Never turn one bad password into
# a login loop: back off after a failure and back off longer when already banned.
QBT_LOGIN_COOLDOWN_SECONDS = 30.0
QBT_LOGIN_BAN_COOLDOWN_SECONDS = 120.0
# qBittorrent 5.0 (Web API 2.11.0) renamed pause/resume to stop/start.
QBT_START_STOP_WEBAPI_VERSION = (2, 11, 0)
# Add-time file priorities exist from Web API 2.11.9 (qBittorrent 5.1).
QBT_ADD_FILE_PRIORITY_WEBAPI_VERSION = (2, 11, 9)
QBT_FILE_SELECTION_VERIFY_ATTEMPTS = 3
QBT_FILE_SELECTION_VERIFY_DELAYS = (0.05, 0.1)

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
    # qBittorrent 5.x uses stoppedDL/stoppedUP for a user-paused torrent.
    if state in PAUSED_STATES:
        return "paused"
    # A seeding torrent is finished. qBittorrent keeps reporting it as "uploading",
    # which must never look like a download in progress.
    if progress >= 1.0:
        return "complete"
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


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Turn ``2.11.0`` into ``(2, 11, 0)`` without adding a dependency."""

    parts = text.strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


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
        self._login_blocked_until = 0.0
        self._login_error_code = "QBT_LOGIN_FAILED"
        self._login_error_message = "qBittorrent login failed"
        self._webapi_version: tuple[int, ...] | None = None
        self._webapi_version_checked = False
        self._modern_start_stop: bool | None = None
        self.metadata_timeout = QBT_METADATA_TIMEOUT_SECONDS
        self.metadata_poll_interval = QBT_METADATA_POLL_SECONDS

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- auth -----------------------------------------------------------------

    def _login_failure(self, exc: EngineError) -> EngineError:
        """Remember a failed login so the next call does not hammer qBittorrent."""

        cooldown = (
            QBT_LOGIN_BAN_COOLDOWN_SECONDS
            if "banned" in exc.message.lower()
            else QBT_LOGIN_COOLDOWN_SECONDS
        )
        self._login_blocked_until = asyncio.get_running_loop().time() + cooldown
        self._login_error_code = exc.code
        self._login_error_message = exc.message
        return exc

    async def login(self) -> None:
        async with self._auth_lock:
            now = asyncio.get_running_loop().time()
            if now < self._login_blocked_until:
                raise EngineError(self._login_error_code, self._login_error_message)
            try:
                await self._login_once()
            except EngineError as exc:
                raise self._login_failure(exc) from exc
            self._login_blocked_until = 0.0

    async def _login_once(self) -> None:
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
        raise_for_status: bool = True,
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
        if raise_for_status and 400 <= response.status_code < 500:
            endpoint = path.rstrip("/").rsplit("/", 1)[-1]
            raise EngineError(
                "QBT_REJECTED",
                f"qBittorrent rejected {endpoint} (HTTP {response.status_code})",
            )
        return response

    # -- engine protocol ------------------------------------------------------

    async def add(self, request: AddRequest) -> str:
        stopped = "true" if request.paused else "false"
        files = {
            "urls": (None, request.url),
            "savepath": (None, request.save_path),
            "category": (None, QBT_CATEGORY),
            "tags": (None, request.tag or ""),
            "sequentialDownload": (None, "false"),
            "firstLastPiecePrio": (None, "true"),
            # 4.x/5.0/5.1 read ``paused``; 5.2+ reads ``stopped``.
            "paused": (None, stopped),
            "stopped": (None, stopped),
        }
        priorities = await self._add_time_priorities(request)
        if priorities is not None:
            files["filePriorities"] = (None, priorities)
        response = await self._request(
            "POST",
            "/api/v2/torrents/add",
            files=files,
        )
        self._check_add_response(response, request.external_id)
        return request.external_id.lower()

    async def _add_time_priorities(self, request: AddRequest) -> str | None:
        """Build ``filePriorities`` only when metadata and a modern API are known."""

        if request.file_indices is None or not request.total_files:
            return None
        version = await self._probe_webapi_version()
        if version is None or version < QBT_ADD_FILE_PRIORITY_WEBAPI_VERSION:
            return None
        total = request.total_files
        chosen = set(request.file_indices)
        return "|".join(
            str(QBT_PRIORITY_NORMAL if index in chosen else QBT_PRIORITY_SKIP)
            for index in range(total)
        )

    def _check_add_response(self, response: httpx.Response, external_id: str) -> None:
        if not 200 <= response.status_code < 300:
            raise EngineError("QBT_REJECTED", "qBittorrent rejected the torrent")

        body = response.text.strip()
        if body == "Ok." or not body:
            return

        if body == "Fails.":
            raise EngineError("QBT_REJECTED", "qBittorrent rejected the torrent")

        try:
            payload = response.json()
        except ValueError:
            raise EngineError("QBT_REJECTED", "qBittorrent rejected the torrent") from None
        if not isinstance(payload, dict):
            raise EngineError("QBT_REJECTED", "qBittorrent rejected the torrent")

        success_count = _as_int(payload.get("success_count")) or 0
        failure_count = _as_int(payload.get("failure_count")) or 0
        pending_count = _as_int(payload.get("pending_count")) or 0
        accepted = failure_count == 0 and (success_count >= 1 or pending_count >= 1)
        if not accepted:
            raise EngineError("QBT_REJECTED", "qBittorrent rejected the torrent")

        added = payload.get("added_torrent_ids")
        if isinstance(added, list):
            normalised = {str(item).lower() for item in added}
            if external_id.lower() not in normalised:
                logger.warning(
                    "qBittorrent add returned different info-hash ids for %s",
                    external_id[:12],
                )

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

    async def _probe_webapi_version(self) -> tuple[int, ...] | None:
        """Read the Web API version once; a qBittorrent restart does not change it."""

        if self._webapi_version_checked:
            return self._webapi_version
        self._webapi_version_checked = True
        try:
            response = await self._request(
                "GET", "/api/v2/app/webapiVersion", raise_for_status=False
            )
        except EngineError:
            return None
        if response.status_code != 200:
            return None
        self._webapi_version = _parse_version(response.text)
        return self._webapi_version

    async def _use_modern_start_stop(self) -> bool:
        if self._modern_start_stop is not None:
            return self._modern_start_stop
        version = await self._probe_webapi_version()
        if version is None:
            # Unknown version: try the new names first and fall back on 404.
            return True
        return version >= QBT_START_STOP_WEBAPI_VERSION

    async def _post_action(self, path: str, external_id: str) -> bool:
        """POST one torrent action. Return False only when the endpoint is missing."""

        response = await self._request(
            "POST",
            path,
            data={"hashes": external_id.lower()},
            raise_for_status=False,
        )
        if 200 <= response.status_code < 300:
            return True
        if response.status_code == 404:
            return False
        endpoint = path.rsplit("/", 1)[-1]
        raise EngineError(
            "QBT_REJECTED",
            f"qBittorrent rejected {endpoint} (HTTP {response.status_code})",
        )

    async def _set_running(self, external_id: str, *, running: bool) -> None:
        modern = await self._use_modern_start_stop()
        if modern:
            primary, fallback = ("start", "resume") if running else ("stop", "pause")
        else:
            primary, fallback = ("resume", "start") if running else ("pause", "stop")

        if await self._post_action(f"/api/v2/torrents/{primary}", external_id):
            self._modern_start_stop = modern
            return
        if await self._post_action(f"/api/v2/torrents/{fallback}", external_id):
            self._modern_start_stop = not modern
            return
        raise EngineError(
            "QBT_REJECTED",
            f"qBittorrent rejected {primary} (HTTP 404)",
        )

    async def stop(self, external_id: str) -> None:
        await self._set_running(external_id, running=False)

    async def start(self, external_id: str) -> None:
        await self._set_running(external_id, running=True)

    async def pause(self, external_id: str) -> None:
        await self.stop(external_id)

    async def resume(self, external_id: str) -> None:
        await self.start(external_id)

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
            raise_for_status=False,
        )
        if response.status_code == 404:
            return []
        if not 200 <= response.status_code < 300:
            raise EngineError(
                "QBT_REJECTED",
                f"qBittorrent rejected files (HTTP {response.status_code})",
            )
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
        start: bool = True,
    ) -> list[int]:
        """Stop, set every file priority, verify them, then optionally start.

        The magnet adds the whole torrent; only file priorities decide what is
        fetched. If any step fails the torrent is left stopped so the pack never
        starts downloading by accident.
        """

        await self.stop(external_id)

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
        expected = {
            item.index: QBT_PRIORITY_NORMAL if item.index in chosen else QBT_PRIORITY_SKIP
            for item in files
        }
        priorities = [str(expected[item.index]) for item in files]
        try:
            await self._request(
                "POST",
                "/api/v2/torrents/filePrio",
                data={
                    "hash": external_id.lower(),
                    "id": "|".join(ids),
                    "priority": "|".join(priorities),
                },
            )
        except EngineError as exc:
            raise EngineError(
                "QBT_FILE_SELECTION_FAILED",
                f"qBittorrent rejected the file selection: {exc.message}",
            ) from exc

        await self._verify_file_priorities(external_id, expected)
        if start:
            await self.start(external_id)
        return sorted(selected)

    async def _verify_file_priorities(self, external_id: str, expected: dict[int, int]) -> None:
        """Read priorities back; qBittorrent applies filePrio asynchronously."""

        for attempt in range(QBT_FILE_SELECTION_VERIFY_ATTEMPTS):
            files = await self.files(external_id)
            actual = {item.index: item.priority for item in files}
            if all(actual.get(index) == priority for index, priority in expected.items()):
                return
            if attempt < len(QBT_FILE_SELECTION_VERIFY_DELAYS):
                await asyncio.sleep(QBT_FILE_SELECTION_VERIFY_DELAYS[attempt])
        raise EngineError(
            "QBT_FILE_SELECTION_FAILED",
            "qBittorrent did not apply the file priorities",
        )

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
