"""aria2 JSON-RPC adapter for direct downloads.

Torrents never go through aria2 (``AGENTS.md`` hard constraint). The router rejects a
``magnet:`` or ``.torrent`` URL before it reaches this module; :meth:`Aria2Engine.add`
checks again so no other caller can bypass the rule.

aria2's API is all strings: every numeric value in a request or response is converted
here, never by the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Sequence
from urllib.parse import urlsplit

import httpx

from daku.config import Settings
from daku.services.engines.base import (
    AddRequest,
    EngineError,
    EngineStatus,
    Health,
)

ARIA2_TIMEOUT_SECONDS = 10.0
STATUS_KEYS = [
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "downloadSpeed",
    "errorMessage",
    "files",
]

STATUS_MAP = {
    "active": "active",
    "waiting": "queued",
    "paused": "paused",
    "complete": "complete",
    "error": "failed",
    "removed": "cancelled",
}


def is_torrent_url(url: str) -> bool:
    """True when a URL must be handed to the torrent engine instead."""

    candidate = (url or "").strip()
    if not candidate:
        return False
    if candidate.lower().startswith(("magnet:", "torrent:")):
        return True
    path = urlsplit(candidate).path.lower()
    return path.endswith(".torrent")


def _as_int(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


class Aria2Engine:
    """Drive the aria2 JSON-RPC endpoint."""

    name = "aria2"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._endpoint = settings.aria2_rpc
        self._secret = settings.aria2_secret
        self._client = client or httpx.AsyncClient(timeout=ARIA2_TIMEOUT_SECONDS)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _params(self, params: list[object]) -> list[object]:
        if self._secret:
            return [f"token:{self._secret}", *params]
        return list(params)

    async def call(self, method: str, params: list[object] | None = None) -> object:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": self._params(params or []),
        }
        try:
            response = await self._client.post(self._endpoint, json=payload)
        except httpx.HTTPError as exc:
            raise EngineError("ARIA2_UNREACHABLE", f"aria2 is unreachable: {exc}") from exc
        if response.status_code != 200:
            raise EngineError("ARIA2_UNREACHABLE", f"aria2 returned HTTP {response.status_code}")
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise EngineError("ARIA2_UNREACHABLE", "aria2 returned invalid JSON") from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise EngineError("ARIA2_RPC_ERROR", str(message or "aria2 RPC error"))
        if not isinstance(data, dict) or "result" not in data:
            raise EngineError("ARIA2_UNREACHABLE", "aria2 returned an unexpected payload")
        return data["result"]

    async def add(self, request: AddRequest) -> str:
        if is_torrent_url(request.url):
            raise EngineError(
                "ARIA2_TORRENT_FORBIDDEN",
                "aria2 is not a torrent engine; send magnets to qBittorrent",
            )
        options: dict[str, str] = {
            "dir": request.save_path,
            "continue": "true",
            "max-connection-per-server": "8",
            "auto-file-renaming": "false",
        }
        if request.filename:
            options["out"] = request.filename
        result = await self.call("aria2.addUri", [[request.url], options])
        gid = str(result or "").strip()
        if not gid:
            raise EngineError("ARIA2_RPC_ERROR", "aria2 did not return a GID")
        return gid

    async def tell_status(self, gid: str) -> EngineStatus:
        try:
            result = await self.call("aria2.tellStatus", [gid, STATUS_KEYS])
        except EngineError as exc:
            message = exc.message.lower()
            if "not found" in message or "gid" in message:
                raise EngineError("ARIA2_GID_MISSING", exc.message) from exc
            raise
        if not isinstance(result, dict):
            raise EngineError("ARIA2_UNREACHABLE", "aria2 returned an unexpected status payload")

        total = _as_int(result.get("totalLength")) or None
        completed = _as_int(result.get("completedLength"))
        speed = _as_int(result.get("downloadSpeed"))
        progress = 0.0
        if total:
            progress = min(max(completed / total, 0.0), 1.0)
        eta: int | None = None
        if speed > 0 and total:
            eta = max(round((total - completed) / speed), 0)

        files: list[str] = []
        raw_files = result.get("files")
        if isinstance(raw_files, list):
            for item in raw_files:
                if isinstance(item, dict) and item.get("path"):
                    files.append(str(item["path"]))

        state = str(result.get("status") or "")
        daku_status = STATUS_MAP.get(state, "active")
        if daku_status == "complete":
            progress = 1.0
        error = result.get("errorMessage")
        return EngineStatus(
            external_id=gid,
            status=daku_status,
            progress=progress,
            downloaded_bytes=completed,
            total_bytes=total,
            speed_bps=speed if speed > 0 and daku_status == "active" else None,
            eta_seconds=eta,
            error=str(error) if error else None,
            files=files,
        )

    async def adopt(self, url: str) -> str | None:
        """Find an in-flight transfer by URL after aria2c restarted.

        A restarted aria2 loads its session but assigns fresh GIDs, so a stored GID
        points at nothing while the payload continues. Matching the source URL against
        the active/waiting/stopped lists reconnects the row to the transfer.
        """

        if not url:
            return None
        keys = ["gid", "status", "files"]
        sources: tuple[tuple[str, list[object]], ...] = (
            ("aria2.tellActive", [keys]),
            ("aria2.tellWaiting", [0, 200, keys]),
            ("aria2.tellStopped", [0, 200, keys]),
        )
        for method, params in sources:
            try:
                result = await self.call(method, params)
            except EngineError:
                continue
            if not isinstance(result, list):
                continue
            for item in result:
                if not isinstance(item, dict):
                    continue
                gid = str(item.get("gid") or "")
                if not gid:
                    continue
                for entry in item.get("files") or []:
                    if not isinstance(entry, dict):
                        continue
                    for uri in entry.get("uris") or []:
                        if isinstance(uri, dict) and uri.get("uri") == url:
                            return gid
        return None

    async def poll(self, external_ids: Sequence[str]) -> dict[str, EngineStatus]:
        ids = [item for item in external_ids if item]
        if not ids:
            return {}
        results = await asyncio.gather(
            *(self.tell_status(gid) for gid in ids), return_exceptions=True
        )
        statuses: dict[str, EngineStatus] = {}
        transient: EngineError | None = None
        for gid, result in zip(ids, results, strict=True):
            if isinstance(result, EngineStatus):
                statuses[gid] = result
            elif isinstance(result, EngineError):
                if result.code == "ARIA2_GID_MISSING":
                    statuses[gid] = EngineStatus(
                        external_id=gid,
                        status="failed",
                        error="download no longer exists in aria2",
                    )
                else:
                    transient = result
        if transient is not None and not statuses:
            raise transient
        return statuses

    async def pause(self, external_id: str) -> None:
        await self.call("aria2.pause", [external_id])

    async def resume(self, external_id: str) -> None:
        await self.call("aria2.unpause", [external_id])

    async def set_file_selection(
        self,
        external_id: str,
        indices: Sequence[int],
        *,
        paths: Sequence[str] | None = None,
    ) -> list[int]:
        """aria2 direct downloads have no torrent file list to select from."""

        raise EngineError(
            "ARIA2_FILE_SELECTION_UNSUPPORTED",
            "File selection is only available for torrent downloads",
        )

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None:
        try:
            await self.call("aria2.remove", [external_id])
        except EngineError as exc:
            # A finished download cannot be removed from the active list; dropping its
            # result below is the correct terminal action.
            if "cannot" not in exc.message.lower() and "not found" not in exc.message.lower():
                raise
        with contextlib.suppress(EngineError):
            await self.call("aria2.removeDownloadResult", [external_id])

    async def health(self) -> Health:
        try:
            result = await self.call("aria2.getVersion")
        except EngineError as exc:
            return Health(ok=False, detail=exc.message)
        version = str(result.get("version") or "unknown") if isinstance(result, dict) else "unknown"
        return Health(ok=True, detail=f"aria2 {version}", version=version)
