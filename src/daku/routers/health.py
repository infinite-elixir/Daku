"""Health router."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from daku import __version__
from daku.config import Settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    started_at: float = request.app.state.started_at
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(max(0.0, time.monotonic() - started_at), 3),
        "data_dir": str(settings.data_dir),
    }
