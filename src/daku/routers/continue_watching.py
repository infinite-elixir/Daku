"""Continue Watching: one card per title, newest played file first."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.repositories import continue_watching as continue_repo
from daku.routers.payloads import media_file_payload

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/continue-watching")
def list_continue_watching(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Read-only: playback progress only, no title or download state is written."""

    items = [
        {
            "title_id": entry.title.id,
            "title": entry.title.title,
            "kind": entry.title.kind,
            "poster_url": entry.title.poster_url,
            "played_at": entry.played_at,
            "up_next": entry.up_next,
            "is_new": entry.is_new,
            "file": media_file_payload(session, entry.file),
        }
        for entry in continue_repo.list_continue_watching(session)
    ]
    return {"items": items}
