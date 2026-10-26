"""Search router: IMDb + Cinemeta for movies/series, AniList for anime."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.search import DEFAULT_LIMIT, search_titles

router = APIRouter(prefix="/api", tags=["search"])

SearchKind = Literal["movie", "series", "anime"]
AnimeFormat = Literal["TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT", "MUSIC"]


@router.get("/search")
async def search(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[
        str,
        Query(min_length=1, max_length=200, description="Free-text title query"),
    ],
    kind: Annotated[SearchKind | None, Query()] = None,
    format: Annotated[
        AnimeFormat | None,
        Query(description="Filter anime results by format (kind=anime only)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be empty")
    if format is not None and kind != "anime":
        raise HTTPException(
            status_code=422,
            detail="format is only supported when kind=anime",
        )

    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    outcome = await search_titles(
        client, cache, q, kind=kind, limit=limit, fmt=format, session=session
    )
    return {
        "query": q,
        "kind": kind,
        "format": format,
        "stale": outcome.stale,
        "results": [candidate.to_dict() for candidate in outcome.results],
    }
