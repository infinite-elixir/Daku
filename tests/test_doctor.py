from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from daku.__main__ import main
from daku.config import Settings
from daku.services.doctor import (
    STATUS_DEGRADED,
    STATUS_NOT_CONFIGURED,
    STATUS_OK,
    probe_engines,
    run_doctor,
)
from daku.services.engines.base import AddRequest, EngineStatus, Health


class FakeEngine:
    def __init__(self, health: Health) -> None:
        self._health = health

    async def health(self) -> Health:
        return self._health

    async def add(self, request: AddRequest) -> str:  # pragma: no cover - protocol filler
        return request.external_id

    async def poll(
        self, external_ids: Sequence[str]
    ) -> dict[str, EngineStatus]:  # pragma: no cover
        return {}

    async def pause(self, external_id: str) -> None:  # pragma: no cover
        return None

    async def resume(self, external_id: str) -> None:  # pragma: no cover
        return None

    async def remove(
        self, external_id: str, *, delete_files: bool = False
    ) -> None:  # pragma: no cover
        return None

    async def aclose(self) -> None:  # pragma: no cover
        return None


async def test_unreachable_qbittorrent_without_a_password_reports_both_faults(
    settings: Settings,
) -> None:
    """The Web UI being off must not be hidden behind "password not set"."""

    engine = FakeEngine(Health(ok=False, detail="qBittorrent is unreachable: refused"))
    checks = await probe_engines(
        settings, {"qbittorrent": engine, "aria2": engine}, aria2_binary_available=True
    )

    assert checks["qbittorrent"].status == STATUS_NOT_CONFIGURED
    assert "password not set" in checks["qbittorrent"].detail
    assert "unreachable" in checks["qbittorrent"].detail


async def test_unreachable_qbittorrent_with_a_password_is_degraded(settings: Settings) -> None:
    configured = settings.model_copy(update={"qbt_pass": "secret"})
    engine = FakeEngine(Health(ok=False, detail="qBittorrent is unreachable: refused"))
    checks = await probe_engines(
        configured, {"qbittorrent": engine, "aria2": engine}, aria2_binary_available=True
    )

    assert checks["qbittorrent"].status == STATUS_DEGRADED
    assert checks["qbittorrent"].detail == "qBittorrent is unreachable: refused"


async def test_qbittorrent_ok_without_a_stored_password_is_ok(settings: Settings) -> None:
    """qBittorrent's localhost auth bypass needs no password."""

    engine = FakeEngine(Health(ok=True, detail="qBittorrent v5.3.0", version="v5.3.0"))
    checks = await probe_engines(
        settings, {"qbittorrent": engine, "aria2": engine}, aria2_binary_available=True
    )

    assert checks["qbittorrent"].status == STATUS_OK


def test_doctor_reports_the_magnet_probe_without_starting_one(settings: Settings) -> None:
    checks = run_doctor(
        settings,
        probe_status={
            "status": "unavailable",
            "queue_depth": 3,
            "last_failure_age_seconds": 12.0,
        },
    )

    probe = next(check for check in checks if check.name == "magnet_probe")
    assert probe.status == "unavailable"
    assert "queue depth 3" in probe.detail
    assert "12s ago" in probe.detail
    assert "daku[probe]" in probe.detail


def test_doctor_reports_a_disabled_probe(settings: Settings) -> None:
    disabled = settings.model_copy(update={"magnet_probe": False})

    checks = run_doctor(disabled, probe_status={"status": "ok", "queue_depth": 0})

    probe = next(check for check in checks if check.name == "magnet_probe")
    assert probe.status == "disabled"


def test_doctor_cli_prints_checks_without_serving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DAKU_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("DAKU_DATA_DIR", str(tmp_path / "data"))

    async def healthy(self: Any) -> Health:
        return Health(ok=True, detail="fake engine ok")

    monkeypatch.setattr("daku.services.engines.qbittorrent.QbittorrentEngine.health", healthy)
    monkeypatch.setattr("daku.services.engines.aria2.Aria2Engine.health", healthy)

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "qbittorrent" in output
    assert "checks ok" in output


def test_doctor_cli_json_and_strict_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DAKU_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("DAKU_DATA_DIR", str(tmp_path / "data"))

    async def unhealthy(self: Any) -> Health:
        return Health(ok=False, detail="qBittorrent is unreachable: refused")

    monkeypatch.setattr("daku.services.engines.qbittorrent.QbittorrentEngine.health", unhealthy)
    monkeypatch.setattr("daku.services.engines.aria2.Aria2Engine.health", unhealthy)

    assert main(["doctor", "--json", "--strict"]) == 1
    payload = json.loads(capsys.readouterr().out)
    names = {check["name"] for check in payload["checks"]}
    assert "qbittorrent" in names
