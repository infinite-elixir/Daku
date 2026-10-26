"""Integration health checks surfaced at ``/api/system/doctor``."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from daku.config import Settings
from daku.repositories import settings as settings_repo
from daku.services.engines.base import DownloadEngine, Health
from daku.services.metadata.mapping import mapping_status
from daku.services.metadata.ratings import (
    STATUS_CONFIGURED,
    STATUS_INVALID_KEY,
    STATUS_UNCONFIGURED,
    OmdbEnricher,
)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"

PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _integration_check(name: str, detail: str, configured: bool) -> Check:
    status = STATUS_OK if configured else STATUS_NOT_CONFIGURED
    return Check(name=name, status=status, detail=detail)


async def _probe(engine: DownloadEngine | None, name: str) -> Health:
    if engine is None:
        return Health(ok=False, detail=f"{name} adapter is not running")
    try:
        return await asyncio.wait_for(engine.health(), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return Health(ok=False, detail=f"{name} did not answer within {PROBE_TIMEOUT_SECONDS:.0f}s")
    except Exception as exc:  # pragma: no cover - health must never raise
        return Health(ok=False, detail=str(exc))


async def probe_engines(
    settings: Settings,
    engines: Mapping[str, DownloadEngine],
    *,
    aria2_binary_available: bool | None = None,
) -> dict[str, Check]:
    """Live qBittorrent/aria2 checks for the doctor endpoint."""

    checks: dict[str, Check] = {}

    # Probe even without a password: "unreachable" (Web UI off, wrong port) and
    # "credentials missing" are different faults and need different fixes.
    health = await _probe(engines.get("qbittorrent"), "qBittorrent")
    if health.ok:
        checks["qbittorrent"] = Check(name="qbittorrent", status=STATUS_OK, detail=health.detail)
    elif not settings.qbt_pass:
        checks["qbittorrent"] = Check(
            name="qbittorrent",
            status=STATUS_NOT_CONFIGURED,
            detail=f"password not set in secrets.json; {health.detail}",
        )
    else:
        checks["qbittorrent"] = Check(
            name="qbittorrent", status=STATUS_DEGRADED, detail=health.detail
        )

    binary_available = (
        aria2_binary_available
        if aria2_binary_available is not None
        else bool(shutil.which(settings.aria2_bin or "aria2c"))
    )
    health = await _probe(engines.get("aria2"), "aria2")
    if health.ok:
        checks["aria2"] = Check(name="aria2", status=STATUS_OK, detail=health.detail)
    elif not binary_available:
        checks["aria2"] = Check(
            name="aria2",
            status=STATUS_ERROR,
            detail="aria2c not found; direct downloads are disabled (torrents still work)",
        )
    else:
        checks["aria2"] = Check(
            name="aria2",
            status=STATUS_DEGRADED,
            detail=f"{health.detail}; direct downloads start aria2c on demand",
        )
    return checks


def run_doctor(
    settings: Settings,
    session: Session | None = None,
    omdb: OmdbEnricher | None = None,
    engine_checks: Mapping[str, Check] | None = None,
    probe_status: Mapping[str, object] | None = None,
) -> list[Check]:
    """Return one check per integration."""

    live = engine_checks or {}
    checks: list[Check] = [
        live.get("qbittorrent")
        or _integration_check(
            "qbittorrent",
            "password not set in secrets.json"
            if not settings.qbt_pass
            else "configured; live probe unavailable",
            bool(settings.qbt_pass),
        ),
        live.get("aria2")
        or _integration_check(
            "aria2",
            "aria2c not configured",
            bool(settings.aria2_rpc),
        ),
        _integration_check(
            "jellyfin",
            "API key not set in secrets.json"
            if not settings.jellyfin_api_key
            else "adapter arrives in T-014",
            bool(settings.jellyfin_api_key),
        ),
        _integration_check(
            "telegram",
            "bot token or archive channel not set"
            if not (settings.telegram_bot_token and settings.telegram_archive_channel)
            else "bot arrives in T-015",
            bool(settings.telegram_bot_token and settings.telegram_archive_channel),
        ),
        Check(
            name="torrentio",
            status=STATUS_OK,
            detail=settings.torrentio_config or "no config prefix (Torrentio defaults)",
        ),
    ]
    anime_sources = (
        settings_repo.get_setting(session, "anime_sources", ["torrentio", "nyaa"])
        if session is not None
        else ["torrentio", "nyaa"]
    )
    checks.append(
        Check(
            name="nyaa",
            status=STATUS_OK if "nyaa" in anime_sources else STATUS_NOT_CONFIGURED,
            detail="enabled for anime RSS searches"
            if "nyaa" in anime_sources
            else "disabled in anime source settings",
        )
    )

    ffprobe = shutil.which("ffprobe")
    checks.append(
        Check(
            name="ffprobe",
            status=STATUS_OK if ffprobe else STATUS_NOT_FOUND,
            detail=ffprobe or "ffprobe not found on PATH; install ffmpeg (sudo apt install ffmpeg)",
        )
    )

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(settings.data_dir)
        free_gb = usage.free / (1024**3)
        checks.append(
            Check(
                name="disk",
                status=STATUS_OK if free_gb >= 1 else STATUS_ERROR,
                detail=f"{free_gb:.1f} GiB free at {settings.data_dir}",
            )
        )
    except OSError as exc:
        checks.append(Check(name="disk", status=STATUS_ERROR, detail=str(exc)))

    checks.append(
        _integration_check(
            "tvdb",
            "TVDB API key not set in secrets.json" if not settings.tvdb_api_key else "configured",
            bool(settings.tvdb_api_key),
        )
    )
    checks.append(
        _integration_check(
            "metadata_sources",
            ", ".join(settings.metadata_sources)
            if settings.metadata_sources
            else "metadata_sources not configured",
            bool(settings.metadata_sources),
        )
    )
    checks.append(_omdb_check(settings, omdb))
    checks.append(_mapping_check(session))
    checks.append(_magnet_probe_check(settings, probe_status))
    return checks


def _magnet_probe_check(settings: Settings, probe_status: Mapping[str, object] | None) -> Check:
    """Report the probe dependency and queue without starting a probe."""

    if not settings.magnet_probe:
        return Check(
            name="magnet_probe",
            status="disabled",
            detail="disabled in settings; release lists and downloads are unaffected",
        )
    data = probe_status or {}
    status = str(data.get("status") or "unavailable")
    queue_depth = data.get("queue_depth")
    last_failure = data.get("last_failure_age_seconds")
    parts = [f"queue depth {queue_depth if queue_depth is not None else 0}"]
    if last_failure is not None:
        parts.append(f"last failure {float(str(last_failure)):.0f}s ago")
    else:
        parts.append("no recent failure")
    if status == "ok":
        detail = "libtorrent available for metadata-only probes; " + ", ".join(parts)
    else:
        detail = "libtorrent extra not installed; install daku[probe] for file lists; " + ", ".join(
            parts
        )
    return Check(name="magnet_probe", status=status, detail=detail)


def _omdb_check(settings: Settings, omdb: OmdbEnricher | None) -> Check:
    """Report the OMDb enrichment state without an unsolicited network probe."""

    if not settings.omdb_api_key:
        status = STATUS_UNCONFIGURED
    elif omdb is not None:
        status = omdb.status(settings.omdb_api_key)
    else:
        status = STATUS_CONFIGURED
    if status == STATUS_INVALID_KEY:
        return Check(name="omdb", status=STATUS_ERROR, detail=STATUS_INVALID_KEY)
    if status == STATUS_UNCONFIGURED:
        return Check(name="omdb", status=STATUS_NOT_CONFIGURED, detail=STATUS_UNCONFIGURED)
    return Check(name="omdb", status=STATUS_OK, detail=STATUS_CONFIGURED)


def _mapping_check(session: Session | None) -> Check:
    if session is None:
        return Check(
            name="mapping",
            status=STATUS_DEGRADED,
            detail="anime mapping dataset not available",
        )
    status = mapping_status(session)
    if status.status == "ok":
        return Check(
            name="mapping",
            status=STATUS_OK,
            detail=(
                f"{status.row_count} rows, schema {status.schema_version or '?'}, "
                f"updated {status.last_update or 'never'}"
            ),
        )
    if status.status == "missing":
        return Check(
            name="mapping",
            status=STATUS_DEGRADED,
            detail="anime mapping dataset not ingested; anime falls back to IMDb ids",
        )
    return Check(
        name="mapping",
        status=STATUS_DEGRADED,
        detail=(
            f"anime mapping dataset is stale ({status.last_update_age_days} days old, "
            f"refresh every {status.refresh_days}); anime falls back to IMDb ids"
        ),
    )


def doctor_payload(
    settings: Settings,
    session: Session | None = None,
    omdb: OmdbEnricher | None = None,
    engine_checks: Mapping[str, Check] | None = None,
    probe_status: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "checks": [
            check.to_dict()
            for check in run_doctor(settings, session, omdb, engine_checks, probe_status)
        ],
    }
