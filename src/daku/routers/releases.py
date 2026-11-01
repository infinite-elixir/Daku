"""Release search, saving, manual magnets and download-later.

Search results come from Torrentio and are ephemeral until the user acts on them. Any
action that needs a row (save or download) goes through ``POST /api/releases`` or
``POST /api/releases/{id}/save``; saving never creates a download or touches disk.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy.orm import Session

from daku.config import DEFAULT_PROVIDERS
from daku.db import get_session
from daku.errors import AppError
from daku.models import Episode, Release, Title
from daku.repositories import cours as cours_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import releases as releases_repo
from daku.repositories import seasons as seasons_repo
from daku.repositories import settings as settings_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata import cours as cours_service
from daku.services.metadata.aliases import effective_aliases
from daku.services.metadata.overrides import load_season_offsets
from daku.services.releases.magnet import MagnetError, parse_magnet
from daku.services.releases.metadata import (
    ResolvedFrom,
    load_metadata,
    metadata_snapshot,
    resolved_from,
)
from daku.services.releases.refresh import RefreshThrottle, refresh_releases
from daku.services.torrents.cache import PayloadCache
from daku.services.torrents.ranking import (
    MIN_QUALITIES,
    QUALITY_DISPLAY_ORDER,
    apply_min_quality,
    dedupe_releases,
    extract_hints,
    quality_counts,
    sort_releases,
)
from daku.services.torrents.sources import search_all_sources
from daku.services.torrents.torrentio import TorrentioError, with_providers, with_qualityfilter

router = APIRouter(prefix="/api", tags=["releases"])

ReleaseSort = Literal["seeders", "quality", "size"]
MinQuality = Literal["480p", "576p", "720p", "1080p", "1440p", "2160p"]


def _blank_to_none(value: Any) -> Any:
    """Treat an explicitly empty ``min_quality=`` as "any", not as invalid input."""

    if isinstance(value, str) and not value.strip():
        return None
    return value


PROVIDER_RE = re.compile(r"^[a-z0-9._-]+$")
MAX_PROVIDERS = 20
LIMIT_STEPS = (20, 50, 100, 200)


class CreateReleaseRequest(BaseModel):
    """Persist a pasted magnet or a chosen Torrentio result.

    ``provider``/``quality``/``size_bytes``/``seeders``/``metadata_json`` are optional
    and let the UI keep the resolved Torrentio metadata instead of re-parsing it.
    """

    magnet: str = Field(min_length=1)
    title_id: str = Field(min_length=1)
    episode_id: str | None = None
    filename: str | None = Field(default=None, max_length=1000)
    provider: str | None = Field(default=None, max_length=64)
    quality: str | None = Field(default=None, max_length=64)
    size_bytes: int | None = Field(default=None, ge=0)
    seeders: int | None = Field(default=None, ge=0)
    metadata_json: dict[str, Any] | None = None
    resolved_from: ResolvedFrom | None = None
    save: bool = True


# --------------------------------------------------------------------------- helpers


def _require_title(session: Session, title_id: str) -> Title:
    title = titles_repo.get_title(session, title_id)
    if title is None:
        raise AppError(
            code="TITLE_NOT_FOUND",
            message=f"Title {title_id} is not in the library",
            status_code=404,
        )
    return title


def _require_episode(session: Session, title: Title, episode_id: str) -> Episode:
    episode = episodes_repo.get_episode(session, episode_id)
    if episode is None or episode.title_id != title.id:
        raise AppError(
            code="EPISODE_NOT_FOUND",
            message=f"Episode {episode_id} is not in this title",
            status_code=404,
        )
    return episode


def _require_release(session: Session, release_id: str) -> Release:
    row = releases_repo.get_release(session, release_id)
    if row is None:
        raise AppError(
            code="RELEASE_NOT_FOUND",
            message=f"Release {release_id} does not exist",
            status_code=404,
        )
    return row


def _media_query(
    title: Title,
    *,
    season: int | None,
    episode: int | None,
    pack: bool,
) -> tuple[str, str]:
    """Translate a library title into a Torrentio ``(type, id)`` pair."""

    imdb_id = title.imdb_id
    if not imdb_id:
        raise AppError(
            code="RELEASE_SOURCE_UNAVAILABLE",
            message="This title has no IMDb id, so Torrentio cannot be queried",
            status_code=422,
            detail={"title_id": title.id, "kind": title.kind},
        )

    if title.kind == "movie":
        if season is not None or episode is not None:
            raise AppError(
                code="INVALID_RELEASE_QUERY",
                message="Movies do not take a season or episode",
                status_code=422,
            )
        return "movie", imdb_id

    if episode is not None:
        if season is None:
            raise AppError(
                code="INVALID_RELEASE_QUERY",
                message="An episode query also needs a season",
                status_code=422,
            )
        return "series", f"{imdb_id}:{season}:{episode}"

    if pack:
        if season is not None:
            return "series", f"{imdb_id}:{season}"
        return "series", imdb_id

    raise AppError(
        code="PACK_REQUIRED",
        message="Pass season and episode, or pack=true for a whole-series/season pack",
        status_code=422,
        detail={"title_id": title.id, "season": season},
    )


def _episode_row(
    session: Session,
    title: Title,
    *,
    season: int | None,
    episode: int | None,
) -> Episode | None:
    if title.kind == "movie" or season is None or episode is None:
        return None
    return episodes_repo.find_episode(session, title_id=title.id, season=season, episode=episode)


def _provider_seasons(session: Session, title: Title, season: int | None) -> list[int]:
    """Provider seasons whose release names can supply a library season (T-052)."""

    if season is None:
        return []
    values: list[int] = []
    row = seasons_repo.get_season(session, title.id, season)
    if row is not None:
        values.append(row.provider_season)
    for cour in cours_repo.list_cours(session, title.id):
        if cour.library_season == season:
            values.append(cour.provider_season)
    for provider_season, library_season in load_season_offsets(session, title.id).items():
        if library_season == season:
            values.append(provider_season)
    if title.anilist_id:
        # A split cour published inside a long franchise may not have a
        # ``title_cours`` row yet. The mapping dataset still knows the
        # provider season that release names use.
        mapped = cours_service.provider_season_for_anilist(session, [title.anilist_id])
        if mapped > 1:
            values.append(mapped)
    return sorted(set(values))


def _existing_rows(session: Session, title: Title) -> dict[tuple[str, str | None], Release]:
    rows: dict[tuple[str, str | None], Release] = {}
    for row in releases_repo.list_releases(session, title.id):
        if row.info_hash:
            rows[(row.info_hash.lower(), row.episode_id)] = row
    return rows


def _quality_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _provider_list(raw: str | None) -> list[str]:
    """Parse and validate a comma-separated provider selection.

    No closed whitelist: Torrentio adds providers and a stale list here would be
    worse than a passthrough. Character set and count are still validated so junk
    never reaches the upstream URL.
    """

    if raw is None or not raw.strip():
        return []
    providers: list[str] = []
    for part in raw.split(","):
        provider = part.strip().lower()
        if not provider:
            continue
        if not PROVIDER_RE.fullmatch(provider):
            raise AppError(
                code="INVALID_PROVIDERS",
                message=f"Invalid provider name: {part.strip()!r}",
                status_code=422,
                detail={"providers": raw},
            )
        if provider not in providers:
            providers.append(provider)
    if len(providers) > MAX_PROVIDERS:
        raise AppError(
            code="INVALID_PROVIDERS",
            message=f"At most {MAX_PROVIDERS} providers may be requested",
            status_code=422,
            detail={"providers": raw},
        )
    return providers


def _configured_providers(config: str) -> list[str]:
    for part in config.split("|"):
        if part.strip().lower().startswith("providers="):
            value = part.split("=", 1)[1]
            return _provider_list(value)
    return []


def _effective_providers(settings: Any, config: str) -> list[str]:
    """Settings.providers wins; otherwise read the ``providers=`` config part."""

    providers = list(getattr(settings, "providers", []) or [])
    if providers:
        return providers
    return _configured_providers(config)


def _snapshot_for_row(row: Release) -> str:
    """Snapshot metadata at save time, never recomputing an existing snapshot."""

    origin = resolved_from(row)
    metadata = load_metadata(row.metadata_json)
    if metadata:
        return metadata_snapshot(metadata, resolved_from=origin)
    return metadata_snapshot(
        extract_hints(row.filename or ""),
        resolved_from=origin,
    )


# ---------------------------------------------------------------------- search list


@router.get("/releases/options")
def release_options(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    kind: Annotated[str | None, Query()] = None,
    title_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Discovery controls with the effective defaults for one media kind."""

    runtime = request.app.state.settings
    resolved_kind = kind
    if title_id:
        title = titles_repo.get_title(session, title_id)
        if title is not None:
            resolved_kind = title.kind
    if resolved_kind not in ("movie", "series", "anime"):
        resolved_kind = "movie"
    defaults = settings_repo.effective_release_defaults(session, runtime, resolved_kind)
    providers = list(dict.fromkeys([*DEFAULT_PROVIDERS, *defaults.providers]))
    return {
        "providers": providers,
        "anime_sources": settings_repo.get_anime_sources(session),
        "quality_labels": list(QUALITY_DISPLAY_ORDER),
        "min_qualities": list(MIN_QUALITIES),
        "sorts": ["seeders", "quality", "size"],
        "defaults": {
            "providers": defaults.providers,
            "min_quality": defaults.min_quality,
            "sort": defaults.sort,
            "limit": defaults.limit,
        },
        "kind": resolved_kind,
        "limit_steps": list(LIMIT_STEPS),
    }


@router.get("/titles/{title_id}/releases")
async def list_releases(
    request: Request,
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
    season: Annotated[int | None, Query(ge=0)] = None,
    episode: Annotated[int | None, Query(ge=1)] = None,
    pack: Annotated[bool, Query()] = False,
    sort: Annotated[ReleaseSort | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    quality_filter: Annotated[
        str | None,
        Query(description="Comma-separated quality labels to drop; empty disables filtering"),
    ] = None,
    providers: Annotated[
        str | None,
        Query(description="Comma-separated providers; empty uses the configured default"),
    ] = None,
    sources: Annotated[
        str | None,
        Query(description="Comma-separated anime sources: torrentio,animetosho"),
    ] = None,
    min_quality: Annotated[
        MinQuality | None,
        BeforeValidator(_blank_to_none),
        Query(description="Minimum quality rank; empty or omitted means any"),
    ] = None,
) -> dict[str, Any]:
    settings = request.app.state.settings
    title = _require_title(session, title_id)
    if title.kind == "anime" and not title.imdb_id:
        media_type, media_id = "series", f"anilist:{title.anilist_id or title.id}"
    else:
        media_type, media_id = _media_query(title, season=season, episode=episode, pack=pack)
    defaults = settings_repo.effective_release_defaults(session, settings, title.kind)

    resolved_sort: str = sort or defaults.sort
    resolved_limit = limit if limit is not None else defaults.limit
    if quality_filter is None:
        blocked = list(settings.quality_filter)
    else:
        blocked = _quality_list(quality_filter)

    requested = _provider_list(providers)
    resolved_min_quality = min_quality if min_quality is not None else defaults.min_quality
    config = with_qualityfilter(settings.torrentio_config, blocked)
    configured_providers = defaults.providers or _effective_providers(settings, config)
    if configured_providers:
        config = with_providers(config, configured_providers)
    if requested:
        config = with_providers(config, requested)

    cache: PayloadCache = request.app.state.releases_cache
    client: httpx.AsyncClient = request.app.state.http_client
    episode_row = _episode_row(session, title, season=season, episode=episode)
    configured_sources = settings_repo.get_anime_sources(session)
    if title.kind == "anime":
        raw_sources = [
            value.strip().lower()
            for value in (sources.split(",") if sources else configured_sources)
            if value.strip()
        ]
        if not raw_sources or any(
            value not in {*settings_repo.ANIME_SOURCES, "nyaa"} for value in raw_sources
        ):
            raise AppError(
                code="INVALID_SOURCES", message="Invalid anime source selection", status_code=422
            )
        selected_sources = settings_repo.normalize_anime_sources(raw_sources)
    else:
        selected_sources = ["torrentio"]
    if "torrentio" in selected_sources and title.kind == "anime" and not title.imdb_id:
        selected_sources = [value for value in selected_sources if value != "torrentio"]
    try:
        outcome = await search_all_sources(
            client,
            cache,
            kind=title.kind,
            title=title.title,
            imdb_id=title.imdb_id,
            season=season,
            episode=episode,
            pack=pack,
            sources=selected_sources,
            torrentio_config=config,
            synonyms=effective_aliases(session, title) if title.kind == "anime" else None,
            absolute=episode_row.absolute if episode_row is not None else None,
            provider_seasons=_provider_seasons(session, title, season),
            season_fallback=len(seasons_repo.list_seasons(session, title.id)) <= 1,
        )
    except TorrentioError as exc:
        raise AppError(
            code="RELEASES_UNAVAILABLE",
            message="All enabled release sources are unreachable and no cached results are available",
            status_code=502,
            detail={"url": exc.url, "reason": exc.detail},
        ) from exc

    # Behaviour order: parse → dedupe → drop blocked labels → min quality → sort → slice.
    # Season packs that contain the requested episode are kept in the same ranked
    # list as exact matches, but flagged so the UI can badge them. They stay
    # sources for one episode, never an exact episode match.
    combined = [*outcome.releases, *outcome.packs]
    deduped, duplicates_removed = dedupe_releases(combined)
    total_unfiltered = len(deduped)
    # A season/batch pack may not carry a video-quality token, but it is still
    # a valid source because the selected episode file is inspected later. Only
    # packs get the ``unknown`` exemption; ordinary rows still obey the filter.
    blocked_lower = {label.strip().lower() for label in blocked if label and label.strip()}
    matched = [
        release
        for release in deduped
        if (release.is_pack and release.quality.lower() == "unknown")
        or release.quality.lower() not in blocked_lower
    ]
    matched = apply_min_quality(matched, resolved_min_quality)
    ranked = sort_releases(matched, resolved_sort)
    visible = ranked[:resolved_limit]
    counts = quality_counts(matched)

    episode_id = episode_row.id if episode_row is not None else None
    rows = _existing_rows(session, title)

    releases: list[dict[str, Any]] = []
    for release in visible:
        item = release.to_dict()
        origin = "animetosho" if release.provider in {"animetosho", "nyaa"} else "torrentio"
        item["metadata_json"] = {**item["metadata_json"], "resolved_from": origin}
        stored = rows.get((release.info_hash, episode_id))
        item.update(
            {
                "release_id": stored.id if stored is not None else None,
                "is_saved": bool(stored is not None and stored.is_saved),
                "dead": bool(stored is not None and stored.dead),
                "saved_at": stored.saved_at if stored is not None else None,
                "last_checked_at": stored.last_checked_at if stored is not None else None,
                "resolved_from": origin,
            }
        )
        releases.append(item)

    return {
        "title_id": title.id,
        "type": media_type,
        "id": media_id,
        "episode_id": episode_id,
        "stale": outcome.stale,
        "cached_at": outcome.cached_at,
        "sort": resolved_sort,
        "quality_filter": blocked,
        "providers": requested or configured_providers,
        "sources": selected_sources,
        "sources_degraded": outcome.sources_degraded,
        "defaults_source": "request" if requested else defaults.source,
        "min_quality": resolved_min_quality,
        "limit": resolved_limit,
        "total": len(matched),
        "total_unfiltered": total_unfiltered,
        "returned": len(visible),
        "duplicates_removed": duplicates_removed,
        "quality_counts": counts,
        "search": {
            "queries": outcome.queries,
            "aliases": outcome.aliases,
            "degraded": outcome.sources_degraded,
        },
        "releases": releases,
    }


@router.post("/releases", status_code=201)
def create_release(
    body: CreateReleaseRequest,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Resolve a magnet, persist it against a title/episode and optionally save it."""

    title = _require_title(session, body.title_id)
    if body.episode_id is not None:
        episode = _require_episode(session, title, body.episode_id)
        episode_id: str | None = episode.id
    else:
        episode_id = None

    try:
        parsed = parse_magnet(body.magnet)
    except MagnetError as exc:
        raise AppError(code="INVALID_MAGNET", message=str(exc), status_code=400) from exc

    if body.resolved_from is not None:
        origin: ResolvedFrom = body.resolved_from
    elif body.provider and body.provider != "manual":
        origin = "torrentio"
    else:
        origin = "manual"

    hints = body.metadata_json if body.metadata_json is not None else parsed.metadata
    snapshot = metadata_snapshot(hints, resolved_from=origin)

    row, created = releases_repo.upsert_release(
        session,
        title_id=title.id,
        episode_id=episode_id,
        info_hash=parsed.info_hash,
        provider=body.provider or parsed.provider,
        magnet=parsed.magnet,
        filename=body.filename or parsed.filename,
        quality=body.quality or parsed.quality,
        size_bytes=body.size_bytes,
        seeders=body.seeders,
        metadata_json=snapshot,
    )
    if body.save:
        saved = releases_repo.save_release(session, row.id, metadata_json=snapshot)
        if saved is not None:
            row = saved

    response.status_code = 201 if created else 200
    return releases_repo.to_payload(row)


@router.post("/releases/{release_id}/save")
def save_release(
    release_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _require_release(session, release_id)
    saved = releases_repo.save_release(session, row.id, metadata_json=_snapshot_for_row(row))
    if saved is None:  # pragma: no cover - row was just fetched
        raise AppError(code="RELEASE_NOT_FOUND", message="Release disappeared", status_code=404)
    return releases_repo.to_payload(saved)


@router.delete("/releases/{release_id}/save")
def unsave_release(
    release_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    _require_release(session, release_id)
    row = releases_repo.unsave_release(session, release_id)
    if row is None:  # pragma: no cover - row was just fetched
        raise AppError(code="RELEASE_NOT_FOUND", message="Release disappeared", status_code=404)
    return releases_repo.to_payload(row)


@router.get("/titles/{title_id}/releases/saved")
async def list_saved_releases(
    request: Request,
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
    episode_id: Annotated[str | None, Query()] = None,
    quality: Annotated[
        str | None, Query(description="Comma-separated quality labels to keep")
    ] = None,
    dead: Annotated[bool | None, Query(description="Filter on the dead flag")] = None,
) -> dict[str, Any]:
    """The Saved tab. Opening it re-checks seeders, at most once per release per 10 min."""

    title = _require_title(session, title_id)
    qualities = _quality_list(quality)
    rows = releases_repo.list_saved(
        session,
        title_id=title.id,
        episode_id=episode_id,
        dead=dead,
        qualities=qualities,
    )

    throttle: RefreshThrottle = request.app.state.release_refresh
    report = await refresh_releases(
        session,
        request.app.state.http_client,
        request.app.state.releases_cache,
        request.app.state.settings,
        rows,
        throttle=throttle,
    )

    # Re-read: refreshes may have changed seeders and the dead flag.
    rows = releases_repo.list_saved(
        session,
        title_id=title.id,
        episode_id=episode_id,
        dead=dead,
        qualities=qualities,
    )
    return {
        "title_id": title.id,
        "total": len(rows),
        "episode_id": episode_id,
        "quality": qualities,
        "dead": dead,
        "refresh": report.to_dict(),
        "releases": [releases_repo.to_payload(row) for row in rows],
    }


@router.post("/releases/{release_id}/refresh")
async def refresh_release(
    request: Request,
    release_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Re-check one release by info-hash. A fetch failure keeps the old seeders."""

    row = _require_release(session, release_id)
    throttle: RefreshThrottle = request.app.state.release_refresh
    report = await refresh_releases(
        session,
        request.app.state.http_client,
        request.app.state.releases_cache,
        request.app.state.settings,
        [row],
        throttle=throttle,
        force=True,
    )
    updated = releases_repo.get_release(session, release_id) or row
    return {
        "refreshed": report.checked > 0,
        "found": report.found > 0,
        "missed": report.missed,
        "stale": report.stale,
        "reason": report.reason,
        "release": releases_repo.to_payload(updated),
    }
