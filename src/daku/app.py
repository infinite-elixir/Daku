"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from daku import __version__
from daku.config import Settings, ensure_secrets_file, load_settings
from daku.db import init_db
from daku.errors import AppError
from daku.events import EventBus
from daku.logging_setup import configure_logging
from daku.routers import (
    downloads,
    events,
    health,
    magnets,
    posters,
    releases,
    search,
    system,
    titles,
)
from daku.routers import settings as settings_router
from daku.services.engines.aria2 import Aria2Engine
from daku.services.engines.qbittorrent import QbittorrentEngine
from daku.services.engines.supervisor import Aria2Supervisor
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import make_client
from daku.services.metadata.mapping import ensure_mapping_settings
from daku.services.metadata.ratings import OmdbEnricher
from daku.services.releases.refresh import RefreshThrottle
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.probe import ProbeService
from daku.worker import Worker

logger = logging.getLogger(__name__)

WEBDIST_DIR = Path(__file__).resolve().parent / "webdist"

PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Daku</title>
  </head>
  <body style="font-family: system-ui; padding: 2rem">
    <h1>Daku</h1>
    <p>The web UI has not been built yet. Run <code>pnpm -C web build</code>.</p>
  </body>
</html>
"""


def _safe_file(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def create_app(settings: Settings | None = None, *, configure_logs: bool = True) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved.ensure_dirs()
        ensure_secrets_file(resolved)
        if configure_logs:
            configure_logging(resolved)
        engine, session_factory = init_db(resolved)
        with session_factory() as session:
            ensure_mapping_settings(session)
        app.state.settings = resolved
        app.state.started_at = time.monotonic()
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.metadata_cache = TTLCache(ttl_seconds=3600)
        app.state.omdb = OmdbEnricher()
        app.state.releases_cache = PayloadCache(ttl_seconds=3600)
        app.state.release_refresh = RefreshThrottle()
        app.state.probe = ProbeService(resolved)
        app.state.http_client = make_client()
        app.state.event_bus = EventBus()

        qbt_engine = QbittorrentEngine(resolved)
        aria2_engine = Aria2Engine(resolved)
        supervisor = Aria2Supervisor(resolved, aria2_engine)
        worker = Worker(
            session_factory,
            resolved,
            {"qbittorrent": qbt_engine, "aria2": aria2_engine},
            supervisor=supervisor,
            bus=app.state.event_bus,
        )
        app.state.qbt_engine = qbt_engine
        app.state.aria2_engine = aria2_engine
        app.state.aria2_supervisor = supervisor
        app.state.worker = worker

        worker_stop = asyncio.Event()
        worker_task = (
            asyncio.create_task(worker.run(worker_stop), name="daku-worker")
            if resolved.worker_enabled
            else None
        )

        logger.info(
            "daku started",
            extra={"version": __version__, "data_dir": str(resolved.data_dir)},
        )
        try:
            yield
        finally:
            if worker_task is not None:
                worker_stop.set()
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
            await supervisor.stop()
            probe = getattr(app.state, "probe", None)
            if probe is not None:
                await probe.aclose()
            await qbt_engine.aclose()
            await aria2_engine.aclose()
            await app.state.http_client.aclose()
            engine.dispose()
            logger.info("daku stopped")

    app = FastAPI(
        title="Daku",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(events.router)
    app.include_router(search.router)
    app.include_router(titles.router)
    app.include_router(releases.router)
    app.include_router(magnets.router)
    app.include_router(settings_router.router)
    app.include_router(downloads.router)
    app.include_router(posters.router)

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    if WEBDIST_DIR.is_dir() and (WEBDIST_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEBDIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str, request: Request) -> FileResponse | HTMLResponse | JSONResponse:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        static_file = _safe_file(WEBDIST_DIR, full_path)
        if static_file is not None:
            return FileResponse(static_file)
        index = WEBDIST_DIR / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(PLACEHOLDER_HTML)

    return app


app = create_app()
