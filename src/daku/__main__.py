"""Entrypoint: ``daku`` serves the app, ``daku db ...`` manages the database,
``daku doctor`` prints the integration health checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn

from daku import __version__
from daku.config import ensure_secrets_file, load_settings
from daku.db import current_version, dump_database_json, init_db
from daku.logging_setup import configure_logging


def _db_command(action: str) -> int:
    settings = load_settings()
    configure_logging(settings)
    engine, _factory = init_db(settings)
    try:
        if action == "dump":
            print(dump_database_json(engine))
        else:
            print(f"{settings.db_path} at schema version {current_version(engine)}")
    finally:
        engine.dispose()
    return 0


def _doctor_command(*, as_json: bool, strict: bool) -> int:
    """Print the same checks as ``GET /api/system/doctor`` without serving the app."""

    from daku.services.doctor import STATUS_DEGRADED, STATUS_ERROR, doctor_payload, probe_engines
    from daku.services.engines.aria2 import Aria2Engine
    from daku.services.engines.qbittorrent import QbittorrentEngine

    settings = load_settings()
    configure_logging(settings, level=logging.WARNING)
    settings.ensure_dirs()
    ensure_secrets_file(settings)

    async def collect() -> dict[str, Any]:
        engine, session_factory = init_db(settings)
        qbt = QbittorrentEngine(settings)
        aria2 = Aria2Engine(settings)
        try:
            checks = await probe_engines(settings, {"qbittorrent": qbt, "aria2": aria2})
            with session_factory() as session:
                return doctor_payload(settings, session, None, checks)
        finally:
            await qbt.aclose()
            await aria2.aclose()
            engine.dispose()

    payload = asyncio.run(collect())
    checks: list[dict[str, str]] = payload["checks"]

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        width = max(len(check["name"]) for check in checks)
        print(f"daku doctor  {payload['checked_at']}")
        for check in checks:
            print(f"  {check['name']:<{width}}  {check['status']:<14}  {check['detail']}")
        unhappy = [check["name"] for check in checks if check["status"] != "ok"]
        print(f"\n{len(checks) - len(unhappy)}/{len(checks)} checks ok")
        if unhappy:
            print(f"needs attention: {', '.join(unhappy)}")

    if strict:
        blocking = {STATUS_ERROR, STATUS_DEGRADED}
        return 1 if any(check["status"] in blocking for check in checks) else 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="daku", description="Daku media librarian")
    parser.add_argument("--version", action="version", version=f"daku {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    db_parser = subparsers.add_parser("db", help="database maintenance")
    db_parser.add_argument("action", choices=["init", "migrate", "dump"])

    doctor_parser = subparsers.add_parser(
        "doctor", help="probe every integration without serving the app"
    )
    doctor_parser.add_argument("--json", action="store_true", help="emit the API payload")
    doctor_parser.add_argument(
        "--strict", action="store_true", help="exit 1 when a check is degraded or failing"
    )

    args = parser.parse_args(argv)

    if args.command == "db":
        return _db_command(args.action)
    if args.command == "doctor":
        return _doctor_command(as_json=args.json, strict=args.strict)

    settings = load_settings()
    configure_logging(settings)

    from daku.app import create_app

    application = create_app(settings, configure_logs=False)
    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
