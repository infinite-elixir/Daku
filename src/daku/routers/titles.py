"""Library CRUD plus the read-only metadata detail endpoints.

``POST /api/titles`` only writes the library. It never creates a download, contacts a
download engine, or touches disk.
"""

from __future__ import annotations

import time
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daku.db import get_session
from daku.errors import AppError
from daku.models import Episode, MediaFile, Title
from daku.repositories import cours as cours_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_files_repo
from daku.repositories import seasons as seasons_repo
from daku.repositories import titles as titles_repo
from daku.services.metadata import anilist, structure
from daku.services.metadata import cours as cours_service
from daku.services.metadata.aliases import (
    build_generated_aliases,
    effective_aliases,
    store_generated_aliases,
)
from daku.services.metadata.anime import episode_map_for_anilist
from daku.services.metadata.anime_identity import validate_anilist_anime
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.classification import (
    AnimeSignal,
    anilist_ids_with_mapping,
    classify_mapped,
    mapping_dataset_present,
    match_exact,
)
from daku.services.metadata.client import UpstreamError
from daku.services.metadata.mapping import MappingError, is_stale, write_override
from daku.services.metadata.models import EpisodeRef, TitleCandidate
from daku.services.metadata.overrides import (
    EDITABLE_FIELDS,
    delete_field_override,
    overrides_bundle,
    set_episode_map,
    update_identity,
)
from daku.services.metadata.ratings import OmdbEnricher, display_payload
from daku.services.metadata.refresh import refresh_title
from daku.services.metadata.resolve import (
    fallback_detail,
    resolve_candidate,
    resolve_candidate_detail,
)
from daku.services.metadata.search import get_anime_detail, get_detail

router = APIRouter(prefix="/api", tags=["library"])

MetaKind = Literal["movie", "series"]
LibraryKind = Literal["movie", "series", "anime"]
AvailabilityFilter = Literal["none", "partial", "complete"]
SortOrder = Literal["added", "title", "year", "kind", "availability"]
TargetProvider = Literal["anilist", "imdb_show", "tvdb_show", "mal"]
MetadataSource = Literal["cinemeta", "tvdb", "anilist"]


class AddTitleRequest(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    source_id: str = Field(min_length=1, max_length=64)
    kind: LibraryKind
    # Optional candidate fields let an add succeed while metadata is unreachable.
    title: str | None = Field(default=None, max_length=500)
    year: int | None = Field(default=None, ge=1800, le=2200)
    poster_url: str | None = None
    banner_url: str | None = None
    wanted: bool = False
    # Chosen on the preview. Persistence lands with T-022 (migration v5); accepting
    # the field now keeps the preview/add contract stable.
    metadata_source: MetadataSource | None = None
    anime: bool = False
    anilist_id: int | None = Field(default=None, ge=1)


class TitlePatchRequest(BaseModel):
    wanted: bool | None = None
    folder_name: str | None = Field(default=None, max_length=255)
    kind: LibraryKind | None = None

    @field_validator("folder_name")
    @classmethod
    def _validate_folder_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if any(separator in cleaned for separator in ("/", "\\", "\x00")):
            raise ValueError("folder_name must be a single directory name")
        return cleaned

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> TitlePatchRequest:
        if not self.model_fields_set:
            raise ValueError("provide at least one of wanted, folder_name or kind")
        return self


class EpisodeFixRequest(BaseModel):
    season: int = Field(ge=0)
    episode: int = Field(ge=1)
    target_episode: int = Field(ge=1)
    target_provider: TargetProvider = "anilist"
    target_id: str | None = None
    target_season: int | None = Field(default=None, ge=0)


class IdentityPatchRequest(BaseModel):
    kind: LibraryKind | None = None
    title: str | None = Field(default=None, max_length=500)
    year: int | None = Field(default=None, ge=1800, le=2200)
    imdb_id: str | None = Field(default=None, max_length=32)
    tvdb_id: str | None = Field(default=None, max_length=32)
    anilist_id: int | None = Field(default=None, ge=1)
    metadata_source: MetadataSource | None = None
    aliases: list[str] | None = None
    confirm: bool = False
    force: bool = False

    @model_validator(mode="after")
    def _require_one_field(self) -> IdentityPatchRequest:
        if not self.model_fields_set - {"confirm", "force"}:
            raise ValueError("provide at least one identity field")
        return self

    def changes(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.model_fields_set
            if field in EDITABLE_FIELDS
        }


class SeasonOffsetRequest(BaseModel):
    provider_season: int = Field(ge=0)
    library_season: int = Field(ge=0)


class EpisodeMapItem(BaseModel):
    season: int = Field(ge=0)
    episode: int = Field(ge=1)
    target_season: int = Field(ge=0)
    target_episode: int = Field(ge=1)
    absolute: int | None = Field(default=None, ge=1)


class EpisodeMapRequest(BaseModel):
    season_offsets: list[SeasonOffsetRequest] = Field(default_factory=list)
    episodes: list[EpisodeMapItem] = Field(default_factory=list)


class SeasonRebuildRequest(BaseModel):
    source: Literal["auto", "imdb", "tvdb", "anilist"] = "auto"
    confirm: bool = False


class SeasonIdentityItem(BaseModel):
    library_season: int = Field(ge=1)
    anilist_id: int = Field(ge=1)


class SeasonsPutRequest(BaseModel):
    seasons: list[SeasonIdentityItem] = Field(min_length=1)


# --------------------------------------------------------------------------- helpers


def _episode_payload(row: Any, available_ids: set[str]) -> dict[str, Any]:
    present = row.id in available_ids
    if present:
        status = "available"
    elif row.status == "available":
        # The row said available but the file is gone; report the truth without
        # clobbering an in-flight queued/downloading state.
        status = "missing"
    else:
        status = row.status
    return {
        "id": row.id,
        "season": row.season,
        "episode": row.episode,
        "label": f"S{row.season:02d}E{row.episode:02d}",
        "absolute": row.absolute,
        "name": row.name,
        "air_date": row.air_date,
        "status": status,
        "present": present,
    }


def _title_payload(
    session: Session,
    title: Title,
    *,
    availability: str | None = None,
    saved_release_count: int | None = None,
    file_count: int | None = None,
) -> dict[str, Any]:
    if availability is None:
        availability = titles_repo.availability_for(session, title)
    if saved_release_count is None:
        saved_release_count = titles_repo.saved_release_counts(session, [title.id]).get(title.id, 0)
    if file_count is None:
        file_count = len(media_files_repo.list_by_title(session, title.id))
    return {
        "id": title.id,
        "kind": title.kind,
        "title": title.title,
        "year": title.year,
        "imdb_id": title.imdb_id,
        "tmdb_id": title.tmdb_id,
        "tvdb_id": title.tvdb_id,
        "anilist_id": title.anilist_id,
        "mal_id": title.mal_id,
        "overview": title.overview,
        "aliases": effective_aliases(session, title),
        "poster_url": title.poster_url,
        "banner_url": title.banner_url,
        "folder_name": title.folder_name,
        "added_at": title.added_at,
        "wanted": title.wanted,
        "wanted_at": title.wanted_at,
        "availability": availability,
        "saved_release_count": saved_release_count,
        "file_count": file_count,
        "has_files": file_count > 0,
    }


def _metadata_state(title: Title, episode_count: int | None = None) -> str:
    """Return 'empty', 'stale', or 'ok'."""
    if title.kind != "movie" and (episode_count or 0) == 0:
        return "empty"
    synced = title.metadata_synced_at
    if synced:
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(synced)
        except (TypeError, ValueError):
            return "ok"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - dt).days
        if age_days > 30:
            return "stale"
    return "ok"


def _seasons_payload(
    session: Session, title: Title, rows: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Explain the library structure: one row per Jellyfin season."""

    if rows is None:
        rows = seasons_repo.list_seasons(session, title.id)
    counts: dict[int, int] = {}
    for episode in episodes_repo.list_episodes(session, title.id):
        counts[episode.season] = counts.get(episode.season, 0) + 1
    if rows:
        return [
            {
                "library_season": row.library_season,
                "provider_season": row.provider_season,
                "source": row.source,
                "anilist_id": row.anilist_id,
                "episode_count": counts.get(row.library_season, 0),
                "absolute_offset": row.absolute_offset,
            }
            for row in rows
        ]
    fallback_source = title.metadata_source or ("anilist" if title.kind == "anime" else "cinemeta")
    return [
        {
            "library_season": season,
            "provider_season": season,
            "source": fallback_source,
            "anilist_id": title.anilist_id if season == 1 else None,
            "episode_count": counts[season],
            "absolute_offset": 0,
        }
        for season in sorted(counts)
    ]


def _detail_payload(session: Session, title: Title) -> dict[str, Any]:
    payload = _title_payload(session, title)
    rows = episodes_repo.list_episodes(session, title.id)
    available_ids = titles_repo.available_episode_ids(session, title.id)
    payload["episode_count"] = len(rows)
    payload["available_episode_count"] = len(available_ids)
    payload["missing_file_count"] = titles_repo.missing_file_count(session, title.id)
    payload["episodes"] = [_episode_payload(row, available_ids) for row in rows]
    payload["seasons"] = _seasons_payload(session, title)
    payload["metadata_state"] = _metadata_state(title, len(rows))
    payload["metadata_source"] = title.metadata_source
    payload["metadata_synced_at"] = title.metadata_synced_at
    signal = (
        classify_mapped(session, title.imdb_id)
        if title.imdb_id
        else AnimeSignal(
            title.kind == "anime",
            "exact" if title.kind == "anime" else "none",
            title.anilist_id,
            "Stored as anime" if title.kind == "anime" else "No AniBridge anime mapping was found",
        )
    )
    payload["classification"] = {
        "is_anime": signal.is_anime,
        "confidence": signal.confidence,
        "reason": signal.reason,
        "suggested_anilist_id": signal.anilist_id,
    }
    return payload


def _preview_episode_payload(
    ref: EpisodeRef, *, kind: str, absolute_offsets: dict[int, int] | None = None
) -> dict[str, Any]:
    """Preview episodes use the library episode shape but have no persisted row."""

    absolute = None
    if kind == "anime" and ref.season != 0:
        absolute = (absolute_offsets or {}).get(ref.season, 0) + ref.episode
    return {
        "id": f"preview:{ref.season}:{ref.episode}",
        "season": ref.season,
        "episode": ref.episode,
        "label": ref.label,
        "absolute": absolute,
        "name": ref.title,
        "air_date": ref.air_date,
        "status": "missing",
    }


def _match_episode_rows(
    old_rows: list[Any], refs: list[EpisodeRef]
) -> list[tuple[Any, EpisodeRef]]:
    """Pair existing rows with new structure without moving a single season onto specials.

    An AniList-only title has one season; rebuilding it from IMDb adds Season 00.
    The regular episodes must keep their ids, so specials are excluded from the
    positional pairing when the old structure had none.
    """

    old_sorted = sorted(old_rows, key=lambda row: (row.season, row.episode))
    new_sorted = sorted(refs, key=lambda ref: (ref.season, ref.episode))
    old_seasons = {row.season for row in old_sorted}
    targets = new_sorted
    if old_seasons == {1} and any(ref.season == 0 for ref in new_sorted):
        targets = [ref for ref in new_sorted if ref.season != 0]
    return list(zip(old_sorted, targets, strict=False))


def _require_title(session: Session, title_id: str) -> Title:
    title = titles_repo.get_title(session, title_id)
    if title is None:
        raise AppError(
            code="TITLE_NOT_FOUND",
            message=f"Title {title_id} is not in the library",
            status_code=404,
        )
    return title


def _publish(request: Request, event_type: str, payload: dict[str, Any]) -> None:
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        bus.publish(event_type, payload)


async def _display_fields(
    request: Request,
    detail: Any,
    *,
    imdb_id: str | None,
    stale: bool,
) -> dict[str, Any]:
    """Merge optional OMDb ratings into a metadata detail. Never writes the DB."""

    enricher: OmdbEnricher = request.app.state.omdb
    settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.http_client
    ratings = await enricher.ratings(client, imdb_id, settings.omdb_api_key)
    return display_payload(detail, ratings, stale=stale)


def _empty_display_fields() -> dict[str, Any]:
    return {
        "imdb_rating": None,
        "rt_rating": None,
        "metacritic_rating": None,
        "runtime_minutes": None,
        "genres": [],
        "cast": [],
        "directors": [],
        "writers": [],
        "studios": [],
        "banner_url": None,
        "stale": True,
        "sources": [],
    }


# --------------------------------------------------------------------------- library


@router.post("/titles")
async def add_title(
    body: AddTitleRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Resolve metadata and add a library entry. Idempotent per provider id."""

    requested_anime = (
        body.anime
        and body.anilist_id is not None
        and body.source
        in {
            "imdb",
            "cinemeta",
            "tvdb",
        }
    )
    existing = titles_repo.find_existing(session, kind=body.kind, source_id=body.source_id)
    if requested_anime and body.source in {"imdb", "cinemeta"}:
        existing_series = titles_repo.find_by_imdb_id(session, body.source_id)
        if existing_series is not None:
            response.status_code = 200
            return _detail_payload(session, existing_series)
    if existing is not None:
        response.status_code = 200
        return _detail_payload(session, existing)

    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    resolved_structure: structure.Structure | None = None
    try:
        if requested_anime:
            detail, _stale = await resolve_candidate_detail(
                client,
                cache,
                source="anilist",
                source_id=str(body.anilist_id),
                kind="anime",
            )
        else:
            detail = await resolve_candidate(
                client,
                cache,
                source=body.source,
                source_id=body.source_id,
                kind=body.kind,
            )
    except AppError as exc:
        if requested_anime:
            raise AppError(
                code="METADATA_UNAVAILABLE",
                message="Anime metadata is temporarily unavailable; nothing was added",
                status_code=502,
            ) from exc
        if exc.code != "METADATA_UNAVAILABLE":
            raise
        # A library entry is not a download: keep the add even when offline.
        detail = fallback_detail(
            source=body.source,
            source_id=body.source_id,
            kind=body.kind,
            title=body.title,
            year=body.year,
            poster_url=body.poster_url,
            banner_url=body.banner_url,
        )

    is_anime = body.kind == "anime" or requested_anime
    imdb_id = (
        body.source_id
        if requested_anime and body.source in {"imdb", "cinemeta"}
        else (None if is_anime else detail.source_id)
    )
    anilist_id = (
        body.anilist_id if requested_anime else (int(detail.source_id) if is_anime else None)
    )
    tvdb_id = body.source_id if body.source == "tvdb" and requested_anime else None
    stored_kind = "anime" if is_anime else body.kind

    split_ranges: list[cours_service.CourRange] = []
    if is_anime:
        split_entries = []
        if anilist_id:
            split_entries = await cours_service.resolve_cours(
                client,
                cache,
                primary_id=int(anilist_id),
                primary=detail,
                deadline=time.monotonic() + 8.0,
            )
        if len(split_entries) > 1:
            provider = cours_service.provider_season_for_anilist(
                session, [entry.anilist_id for entry in split_entries]
            )
            split_ranges = cours_service.build_ranges(split_entries, provider_season=provider)
            detail.episodes = cours_service.combined_refs(split_ranges)
            resolved_structure = structure.Structure(
                source="anilist", source_id=str(anilist_id), episodes=list(detail.episodes)
            )
        else:
            resolved_structure = await structure.hydrate_structure(
                client,
                cache,
                kind=stored_kind,
                imdb_id=imdb_id,
                tvdb_id=tvdb_id,
                anilist_id=anilist_id,
                detail=detail,
            )
        if resolved_structure.episodes:
            detail.episodes = resolved_structure.episodes

    specs = (
        [structure.SeasonSpec(**row) for row in cours_service.season_specs(split_ranges)]
        if split_ranges
        else (
            structure.season_specs(
                session,
                episodes=list(detail.episodes),
                source=resolved_structure.source if resolved_structure else "anilist",
                source_id=(
                    resolved_structure.source_id if resolved_structure else str(anilist_id or "")
                ),
                default_anilist_id=anilist_id,
            )
            if is_anime
            else []
        )
    )
    library_episodes = (
        structure.library_refs(list(detail.episodes), specs) if is_anime else list(detail.episodes)
    )
    absolute = structure.absolute_offsets(specs) if is_anime else None
    try:
        title = titles_repo.create_title(
            session,
            kind=stored_kind,
            title=detail.title,
            year=detail.year,
            imdb_id=imdb_id,
            anilist_id=anilist_id,
            tvdb_id=tvdb_id,
            mal_id=detail.id_mal,
            overview=detail.overview,
            poster_url=detail.poster_url,
            banner_url=detail.banner_url,
            metadata_source="anilist" if is_anime else body.metadata_source,
            wanted=body.wanted,
        )
    except IntegrityError:
        # A concurrent add won the unique index race; return that row instead.
        session.rollback()
        existing = titles_repo.find_existing(session, kind=body.kind, source_id=body.source_id)
        if existing is None:
            raise
        response.status_code = 200
        return _detail_payload(session, existing)

    episodes_repo.sync_episodes(
        session,
        title_id=title.id,
        episodes=library_episodes,
        absolute_from_number=is_anime,
        season_offsets=absolute,
    )
    if is_anime and specs:
        seasons_repo.replace_seasons(
            session,
            title.id,
            [
                {
                    "library_season": spec.library_season,
                    "provider_season": spec.provider_season,
                    "source": spec.source,
                    "source_id": spec.source_id,
                    "anilist_id": spec.anilist_id,
                    "absolute_offset": spec.absolute_offset,
                }
                for spec in specs
            ],
        )
    if is_anime:
        if split_ranges:
            cours_repo.replace_cours(session, title.id, cours_service.cour_rows(split_ranges))
        else:
            cours_repo.delete_cours(session, title.id)
    alias_details = [detail]
    alias_details.extend(
        item.entry.detail for item in split_ranges if item.entry.detail is not None
    )
    store_generated_aliases(
        session,
        title,
        build_generated_aliases(title.title, alias_details),
    )
    session.commit()
    response.status_code = 201
    return _detail_payload(session, title)


@router.get("/titles")
def list_titles(
    session: Annotated[Session, Depends(get_session)],
    kind: Annotated[LibraryKind | None, Query()] = None,
    availability: Annotated[AvailabilityFilter | None, Query()] = None,
    wanted: Annotated[bool | None, Query()] = None,
    has_files: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[SortOrder, Query()] = "added",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    rows, total = titles_repo.query_library(
        session,
        kind=kind,
        wanted=wanted,
        availability=availability,
        has_files=has_files,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    title_ids = [row.id for row in rows]
    availability_by_id = titles_repo.availability_map(session, title_ids)
    saved_by_id = titles_repo.saved_release_counts(session, title_ids)
    files_by_id = titles_repo.file_counts(session, title_ids)
    items = [
        _title_payload(
            session,
            row,
            availability=availability_by_id.get(row.id, "none"),
            saved_release_count=saved_by_id.get(row.id, 0),
            file_count=files_by_id.get(row.id, 0),
        )
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/titles/{title_id}/overrides")
def get_title_overrides(
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    return overrides_bundle(session, title)


@router.patch("/titles/{title_id}/identity")
async def patch_title_identity(
    title_id: str,
    body: IdentityPatchRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    changes = body.changes()
    previous_kind = title.kind

    prospective_kind = changes.get("kind", title.kind)
    prospective_anilist = changes.get("anilist_id", title.anilist_id)
    should_validate = "anilist_id" in changes or ("kind" in changes and prospective_kind == "anime")
    if should_validate and prospective_anilist is not None:
        await validate_anilist_anime(
            request.app.state.http_client,
            request.app.state.metadata_cache,
            int(prospective_anilist),
        )

    title, needs_refresh = update_identity(
        session, title, changes, force=body.force or body.confirm
    )

    refresh_result: dict[str, Any] | None = None
    refresh_error: dict[str, str] | None = None
    if needs_refresh:
        try:
            refresh_result = await refresh_title(
                session,
                title,
                client=request.app.state.http_client,
                cache=request.app.state.metadata_cache,
                settings=request.app.state.settings,
                source="auto",
                renumber=previous_kind != "movie" and title.kind == "anime",
            )
        except AppError as exc:
            refresh_error = {"code": exc.code, "message": exc.message}

    _publish(
        request,
        "title.identity_updated",
        {"title_id": title.id, "fields": sorted(changes), "kind": title.kind},
    )
    _publish(request, "metadata.fixed", {"title_id": title.id, "fields": sorted(changes)})

    payload = _detail_payload(session, title)
    payload["overrides"] = overrides_bundle(session, title)
    payload["refresh"] = refresh_result
    if refresh_error is None and refresh_result is not None:
        reported = refresh_result.get("refresh_error")
        if isinstance(reported, dict) and reported.get("code"):
            refresh_error = {
                "code": str(reported["code"]),
                "message": str(reported.get("message") or "Metadata refresh was incomplete"),
            }
    if refresh_error is not None:
        payload["refresh_error"] = refresh_error
    return payload


@router.put("/titles/{title_id}/episode-map")
def put_title_episode_map(
    title_id: str,
    body: EpisodeMapRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    try:
        set_episode_map(
            session,
            title,
            offsets=[item.model_dump() for item in body.season_offsets],
            episodes=[item.model_dump() for item in body.episodes],
        )
    except AppError:
        raise
    _publish(
        request,
        "metadata.fixed",
        {
            "title_id": title.id,
            "fields": ["season_offsets", "episodes"],
        },
    )
    payload = _detail_payload(session, title)
    payload["overrides"] = overrides_bundle(session, title)
    return payload


@router.delete("/titles/{title_id}/overrides/{field}")
def delete_title_override(
    title_id: str,
    field: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    removed = delete_field_override(session, title.id, field)
    _publish(
        request, "metadata.fixed", {"title_id": title.id, "fields": [field], "removed": removed}
    )
    payload = _detail_payload(session, title)
    payload["overrides"] = overrides_bundle(session, title)
    return payload


def _media_file_payload(session: Session, row: MediaFile) -> dict[str, Any]:
    episode = session.get(Episode, row.episode_id) if row.episode_id else None
    path = media_files_repo.usable_path(row)
    return {
        "id": row.id,
        "download_id": row.download_id,
        "episode_id": row.episode_id,
        "episode_label": f"S{episode.season:02d}E{episode.episode:02d}" if episode else None,
        "episode_name": episode.name if episode else None,
        "season": episode.season if episode else None,
        "episode": episode.episode if episode else None,
        "file_name": path.name if path is not None else Path(row.src_path).name,
        "size_bytes": row.size_bytes,
        "is_primary": row.is_primary,
        "has_episode": episode is not None,
        "playback": _playback_payload(row),
    }


def _playback_payload(row: MediaFile) -> dict[str, Any] | None:
    if row.playback_position_s is None:
        return None
    duration = row.playback_duration_s or row.duration_s
    return {
        "position_s": row.playback_position_s,
        "duration_s": duration,
        "updated_at": row.playback_updated_at,
    }


@router.get("/titles/{title_id}/files")
def list_title_files(
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Every media file Daku filed for this title that still exists on disk."""

    title = _require_title(session, title_id)
    items: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    seasons: dict[int, dict[str, int]] = {}
    total_size = 0
    for row in media_files_repo.list_by_title(session, title.id):
        if not media_files_repo.is_present(row):
            continue
        entry = _media_file_payload(session, row)
        size = int(entry["size_bytes"] or 0)
        total_size += size
        filename = str(entry["file_name"]).casefold()
        season = entry["season"]
        episode_number = entry["episode"]
        if isinstance(season, int) and isinstance(episode_number, int):
            order = (0, season, episode_number, filename)
            aggregate = seasons.setdefault(
                season, {"library_season": season, "file_count": 0, "total_size_bytes": 0}
            )
            aggregate["file_count"] += 1
            aggregate["total_size_bytes"] += size
        else:
            order = (1, 0, 0, filename)
        items.append((order, entry))

    items.sort(key=lambda pair: pair[0])
    return {
        "title_id": title.id,
        "file_count": len(items),
        "total_size_bytes": total_size,
        "seasons": [seasons[key] for key in sorted(seasons)],
        "items": [entry for _order, entry in items],
    }


@router.get("/titles/{title_id}")
def get_library_title(
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    return _detail_payload(session, _require_title(session, title_id))


@router.post("/titles/{title_id}/media/reconcile")
def reconcile_title_media(
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, int]:
    """Forget media rows whose file no longer exists.

    Explicit repair only: this never runs on a read and never deletes a file,
    title, episode or release.
    """

    title = _require_title(session, title_id)
    removed, kept = media_files_repo.reconcile_title(session, title.id)
    return {"removed": removed, "kept": kept}


@router.get("/titles/{title_id}/metadata")
async def get_title_metadata(
    title_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Read-only ratings and rich metadata for a library title.

    Ratings are display-only: nothing is persisted and ``metadata_source`` is untouched.
    A metadata outage returns empty fields rather than an error so the page still renders.
    """

    title = _require_title(session, title_id)
    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client

    detail: Any = None
    stale = False
    try:
        if title.kind == "anime" and title.anilist_id:
            detail, stale = await get_anime_detail(client, cache, int(title.anilist_id))
        elif title.imdb_id:
            meta_kind = "movie" if title.kind == "movie" else "series"
            detail, stale = await get_detail(client, cache, meta_kind, title.imdb_id)
    except AppError:
        detail = None
        stale = True

    if detail is None:
        return _empty_display_fields()

    return await _display_fields(request, detail, imdb_id=title.imdb_id, stale=stale)


@router.patch("/titles/{title_id}")
def patch_title(
    title_id: str,
    body: TitlePatchRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)

    if "wanted" in body.model_fields_set and body.wanted is not None:
        updated = titles_repo.set_wanted(session, title.id, body.wanted)
        if updated is not None:
            title = updated

    updates: dict[str, Any] = {}
    if "folder_name" in body.model_fields_set:
        updates["folder_name"] = body.folder_name
    if "kind" in body.model_fields_set and body.kind is not None:
        updates["kind"] = body.kind
    if updates:
        updated = titles_repo.update_title(session, title.id, **updates)
        if updated is not None:
            title = updated

    return _detail_payload(session, title)


@router.delete("/titles/{title_id}")
def delete_library_title(
    title_id: str,
    session: Annotated[Session, Depends(get_session)],
    keep_files: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    title = _require_title(session, title_id)

    files_deleted = 0
    if not keep_files:
        paths: set[Path] = set()
        for row in media_files_repo.list_by_title(session, title.id):
            for raw in (row.lib_path, row.src_path):
                if raw:
                    paths.add(Path(raw))
        for path in paths:
            try:
                if path.is_file():
                    path.unlink()
                    files_deleted += 1
            except OSError:
                # A missing or locked file must not block the database cascade.
                continue

    titles_repo.delete_title(session, title.id)
    return {"id": title_id, "deleted": True, "files_deleted": files_deleted}


# --------------------------------------------------------------------- season structure


async def _rebuild_structure(
    request: Request,
    session: Session,
    title: Title,
    *,
    requested_source: str,
    confirm: bool,
) -> dict[str, Any]:
    if title.kind == "movie":
        raise AppError(
            code="NOT_A_SERIES",
            message="Movies do not have a season structure",
            status_code=422,
        )

    chosen = requested_source
    if chosen == "auto":
        if title.imdb_id:
            chosen = "imdb"
        elif title.tvdb_id:
            chosen = "tvdb"
        elif title.anilist_id:
            chosen = "anilist"
        else:
            raise AppError(
                code="NO_STRUCTURE_SOURCE",
                message="This title has no IMDb, TVDB or AniList id to rebuild from",
                status_code=422,
            )

    prefer = "cinemeta" if chosen in {"imdb", "cinemeta"} else chosen
    client: httpx.AsyncClient = request.app.state.http_client
    cache: TTLCache = request.app.state.metadata_cache
    struct = await structure.hydrate_structure(
        client,
        cache,
        kind=title.kind,
        imdb_id=title.imdb_id,
        tvdb_id=title.tvdb_id,
        anilist_id=title.anilist_id,
        prefer=prefer,
    )
    if not struct.episodes:
        raise AppError(
            code="METADATA_UNAVAILABLE",
            message="The structure provider returned no episodes; nothing was changed",
            status_code=502,
        )

    specs = structure.season_specs(
        session,
        episodes=list(struct.episodes),
        source=struct.source,
        source_id=struct.source_id,
        default_anilist_id=title.anilist_id,
    )
    refs = structure.library_refs(list(struct.episodes), specs)
    absolute = structure.absolute_offsets(specs)

    old_rows = episodes_repo.list_episodes(session, title.id)
    pairs = _match_episode_rows(old_rows, refs)
    matched_ids = {row.id for row, _ref in pairs}
    unmatched = [row for row in old_rows if row.id not in matched_ids]
    unprotected = [row.id for row in unmatched]
    files_at_risk = 0
    if unprotected:
        files_at_risk = int(
            session.scalar(
                select(func.count(MediaFile.id)).where(MediaFile.episode_id.in_(unprotected))
            )
            or 0
        )
    if files_at_risk and not confirm:
        raise AppError(
            code="REBUILD_REQUIRES_CONFIRM",
            message=("This rebuild would drop episodes that still have files; confirm to continue"),
            status_code=409,
            detail={"episodes": len(unmatched), "files": files_at_risk},
        )

    for row, ref in pairs:
        row.season = ref.season
        row.episode = ref.episode
        row.absolute = None if ref.season == 0 else absolute.get(ref.season, 0) + ref.episode
        if ref.title is not None:
            row.name = ref.title
        if ref.air_date is not None:
            row.air_date = ref.air_date
    session.flush()

    episodes_repo.sync_episodes(
        session,
        title_id=title.id,
        episodes=refs,
        absolute_from_number=True,
        season_offsets=absolute,
    )
    seasons_repo.replace_seasons(
        session,
        title.id,
        [
            {
                "library_season": spec.library_season,
                "provider_season": spec.provider_season,
                "source": spec.source,
                "source_id": spec.source_id,
                "anilist_id": spec.anilist_id,
                "absolute_offset": spec.absolute_offset,
            }
            for spec in specs
        ],
    )
    _publish(
        request,
        "metadata.fixed",
        {"title_id": title.id, "fields": ["seasons"], "source": struct.source},
    )
    payload = _detail_payload(session, title)
    payload["rebuild"] = {
        "source": struct.source,
        "kept_episodes": len(matched_ids),
        "renumbered": len(pairs),
        "created": max(0, len(refs) - len(pairs)),
        "unmatched": len(unmatched),
        "files_at_risk": files_at_risk,
    }
    return payload


@router.post("/titles/{title_id}/seasons/rebuild")
async def rebuild_title_seasons(
    title_id: str,
    body: SeasonRebuildRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    return await _rebuild_structure(
        request,
        session,
        title,
        requested_source=body.source,
        confirm=body.confirm,
    )


@router.put("/titles/{title_id}/seasons")
async def put_title_seasons(
    title_id: str,
    body: SeasonsPutRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Replace the per-season AniList identity set for an AniList-only title."""

    title = _require_title(session, title_id)
    if title.kind != "anime":
        raise AppError(
            code="NOT_ANIME",
            message="Per-season AniList identity is only available for anime titles",
            status_code=422,
        )
    items = sorted(body.seasons, key=lambda item: item.library_season)
    seen_ids: set[int] = set()
    for item in items:
        if item.anilist_id in seen_ids:
            raise AppError(
                code="DUPLICATE_SEASON",
                message=f"AniList id {item.anilist_id} is listed twice",
                status_code=422,
            )
        seen_ids.add(item.anilist_id)
    expected = list(range(1, len(items) + 1))
    if [item.library_season for item in items] != expected:
        raise AppError(
            code="NON_CONTIGUOUS_SEASONS",
            message="Library seasons must be contiguous starting at 1",
            status_code=422,
        )

    client: httpx.AsyncClient = request.app.state.http_client
    cache: TTLCache = request.app.state.metadata_cache
    refs: list[EpisodeRef] = []
    specs: list[dict[str, Any]] = []
    running = 0
    for item in items:
        detail, _stale = await resolve_candidate_detail(
            client,
            cache,
            source="anilist",
            source_id=str(item.anilist_id),
            kind="anime",
        )
        season_refs = [
            EpisodeRef(
                season=item.library_season,
                episode=ref.episode,
                title=ref.title,
                air_date=ref.air_date,
            )
            for ref in detail.episodes
        ]
        refs.extend(season_refs)
        specs.append(
            {
                "library_season": item.library_season,
                "provider_season": 1,
                "source": "anilist",
                "source_id": str(item.anilist_id),
                "anilist_id": item.anilist_id,
                "absolute_offset": running,
                "episode_count": len(season_refs),
            }
        )
        running += len(season_refs)

    absolute = {spec["library_season"]: spec["absolute_offset"] for spec in specs}
    episodes_repo.sync_episodes(
        session,
        title_id=title.id,
        episodes=refs,
        absolute_from_number=True,
        season_offsets=absolute,
    )
    seasons_repo.replace_seasons(session, title.id, specs)
    _publish(
        request,
        "metadata.fixed",
        {"title_id": title.id, "fields": ["seasons"], "source": "anilist"},
    )
    payload = _detail_payload(session, title)
    payload["rebuild"] = {
        "source": "anilist",
        "kept_episodes": len(refs),
        "renumbered": 0,
        "created": len(refs),
        "unmatched": 0,
        "files_at_risk": 0,
    }
    return payload


# ------------------------------------------------------------------ anime mapping API


async def _exact_preview_signal(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    detail: Any,
    kind: str,
    source: str,
    session: Session,
) -> AnimeSignal:
    """Conservative AniList exact match for a preview that has no mapping."""

    if kind != "series" or source not in {"imdb", "cinemeta"}:
        return AnimeSignal(False, "none", None, "No conservative anime match was found")
    candidate = TitleCandidate(
        source=source,
        source_id=detail.source_id,
        kind="series",
        title=detail.title,
        year=detail.year,
    )
    try:
        media = await anilist.search(client, cache, detail.title, limit=10)
    except UpstreamError:
        media = anilist.cached_search(cache, detail.title, limit=10)
    preferred = anilist_ids_with_mapping(
        session,
        [int(item.source_id) for item in media if item.source_id.isdigit()],
    )
    return match_exact(candidate, media, preferred)


@router.get("/metadata/anilist/{anilist_id}")
async def anilist_candidate(
    anilist_id: Annotated[int, PathParam(ge=1)],
    request: Request,
) -> dict[str, Any]:
    """Inspect an AniList id before a manual identity edit saves it (T-052)."""

    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    media = await anilist.probe_media(client, cache, anilist_id)
    if media is None:
        raise AppError(
            code="ANILIST_NOT_FOUND",
            message=f"AniList id {anilist_id} does not exist",
            status_code=404,
            detail={"anilist_id": anilist_id},
        )
    media_type = str(media.get("type") or "").upper()
    raw_title = media.get("title")
    title: dict[str, Any] = raw_title if isinstance(raw_title, dict) else {}
    display = next(
        (
            str(title[key]).strip()
            for key in ("english", "romaji", "native")
            if isinstance(title.get(key), str) and str(title[key]).strip()
        ),
        f"AniList {anilist_id}",
    )
    return {
        "anilist_id": anilist_id,
        "type": media_type or None,
        "format": media.get("format"),
        "episodes": media.get("episodes"),
        "season_year": media.get("seasonYear"),
        "title": display,
        "is_anime": media_type == "ANIME",
    }


@router.get("/metadata/preview")
async def metadata_preview(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    source: Annotated[str, Query(min_length=1, max_length=32)],
    source_id: Annotated[str, Query(min_length=1, max_length=64)],
    kind: Annotated[LibraryKind, Query()],
) -> dict[str, Any]:
    """Read-only metadata view for a search candidate. Never writes."""

    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    detail, stale = await resolve_candidate_detail(
        client,
        cache,
        source=source,
        source_id=source_id,
        kind=kind,
    )

    existing = titles_repo.find_existing(session, kind=kind, source_id=source_id)
    signal = (
        classify_mapped(session, detail.source_id)
        if kind in {"series", "movie"} and source in {"imdb", "cinemeta"}
        else AnimeSignal(
            kind == "anime",
            "exact" if kind == "anime" else "none",
            int(detail.source_id) if kind == "anime" else None,
            "Matched AniList metadata"
            if kind == "anime"
            else "No AniBridge anime mapping was found",
        )
    )
    if not signal.is_anime and mapping_dataset_present(session):
        signal = await _exact_preview_signal(
            client, cache, detail=detail, kind=kind, source=source, session=session
        )
    if signal.is_anime and signal.anilist_id is not None:
        identity = detail
        try:
            identity, stale = await get_anime_detail(client, cache, signal.anilist_id)
        except AppError:
            identity = detail
        if kind == "series":
            struct = await structure.hydrate_structure(
                client,
                cache,
                kind="series",
                imdb_id=source_id if source in {"imdb", "cinemeta"} else None,
                anilist_id=signal.anilist_id,
                detail=identity,
            )
        else:
            struct = await structure.hydrate_structure(
                client,
                cache,
                kind="anime",
                anilist_id=signal.anilist_id,
                detail=identity,
            )
        if struct.episodes:
            detail.episodes = struct.episodes
        detail.title = identity.title or detail.title
        detail.year = identity.year if identity.year is not None else detail.year
        detail.overview = identity.overview or detail.overview
        detail.poster_url = identity.poster_url or detail.poster_url
        detail.banner_url = identity.banner_url or detail.banner_url
        detail.id_mal = identity.id_mal or detail.id_mal
        structure_source = struct.source
        structure_id = struct.source_id
    else:
        structure_source = detail.source
        structure_id = detail.source_id

    mapped_anime = signal.is_anime and kind == "series"
    is_anime = kind == "anime"
    season_specs = structure.season_specs(
        session,
        episodes=list(detail.episodes),
        source=structure_source,
        source_id=structure_id,
        default_anilist_id=signal.anilist_id,
    )
    if signal.is_anime:
        detail.episodes = structure.library_refs(list(detail.episodes), season_specs)
    absolute = structure.absolute_offsets(season_specs)
    episodes = [
        _preview_episode_payload(
            ref,
            kind="anime" if signal.is_anime else kind,
            absolute_offsets=absolute,
        )
        for ref in detail.episodes
    ]
    seasons_payload = [
        {
            "library_season": spec.library_season,
            "provider_season": spec.provider_season,
            "source": spec.source,
            "anilist_id": spec.anilist_id,
            "episode_count": spec.episode_count,
            "absolute_offset": spec.absolute_offset,
        }
        for spec in season_specs
    ]
    preview_source = source if mapped_anime else detail.source
    preview_source_id = source_id if mapped_anime else detail.source_id
    payload: dict[str, Any] = {
        "id": existing.id if existing is not None else None,
        "source": preview_source,
        "source_id": preview_source_id,
        "library_title_id": existing.id if existing is not None else None,
        "stale": stale,
        "kind": "series" if mapped_anime else detail.kind,
        "title": detail.title,
        "year": detail.year,
        "imdb_id": None if is_anime else source_id,
        "tmdb_id": None,
        "tvdb_id": None,
        "anilist_id": signal.anilist_id if signal.is_anime else None,
        "mal_id": detail.id_mal,
        "overview": detail.overview,
        "poster_url": detail.poster_url,
        "banner_url": detail.banner_url,
        "folder_name": existing.folder_name if existing is not None else None,
        "added_at": existing.added_at if existing is not None else None,
        "wanted": existing.wanted if existing is not None else False,
        "wanted_at": existing.wanted_at if existing is not None else None,
        "availability": (
            titles_repo.availability_for(session, existing) if existing is not None else "none"
        ),
        "saved_release_count": (
            titles_repo.saved_release_counts(session, [existing.id]).get(existing.id, 0)
            if existing is not None
            else 0
        ),
        "episode_count": len(episodes),
        "available_episode_count": (
            len(titles_repo.available_episode_ids(session, existing.id))
            if existing is not None
            else 0
        ),
        "episodes": episodes,
        "seasons": seasons_payload,
    }
    payload["classification"] = {
        "is_anime": signal.is_anime,
        "confidence": signal.confidence,
        "reason": signal.reason,
        "suggested_anilist_id": signal.anilist_id,
    }
    if signal.is_anime and signal.anilist_id is not None:
        payload["anime"] = True
        payload["anilist_id"] = signal.anilist_id
    imdb_for_ratings = None if is_anime else detail.source_id
    if imdb_for_ratings is None and existing is not None:
        imdb_for_ratings = existing.imdb_id
    payload.update(await _display_fields(request, detail, imdb_id=imdb_for_ratings, stale=stale))
    return payload


@router.get("/metadata/anime/{anilist_id}/episode-map")
def anime_episode_map(
    anilist_id: Annotated[int, PathParam(ge=1)],
    session: Annotated[Session, Depends(get_session)],
    episode: Annotated[int, Query(ge=1)],
    season: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """Map a season/episode to AniList, or an AniList episode back to a season.

    With ``season`` the source is the IMDb/TVDB season numbering. Without it the
    ``episode`` is an AniList episode and the result carries the source scope.
    """

    mapping = episode_map_for_anilist(session, anilist_id, season=season, episode=episode)
    if mapping is None:
        raise AppError(
            code="MAPPING_NOT_FOUND",
            message="No anime episode mapping found for that episode",
            status_code=404,
            detail={"anilist_id": anilist_id, "season": season, "episode": episode},
        )
    payload = mapping.to_dict()
    payload["anilist_id"] = anilist_id
    payload["stale"] = is_stale(session)
    return payload


@router.post("/titles/{title_id}/episode-fix")
def fix_title_episode(
    title_id: str,
    body: EpisodeFixRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    try:
        row = write_override(
            session,
            title=title,
            season=body.season,
            episode=body.episode,
            target_episode=body.target_episode,
            target_provider=body.target_provider,
            target_id=body.target_id,
            target_season=body.target_season,
        )
    except MappingError as exc:
        raise AppError(code=exc.code, message=exc.message, status_code=422) from exc
    return {
        "title_id": title.id,
        "override": {
            "id": row.id,
            "season": row.season,
            "episode": row.episode,
            "target_provider": row.target_provider,
            "target_id": row.target_id,
            "target_season": row.target_season,
            "target_episode": row.target_episode,
            "created_at": row.created_at,
        },
    }


class RefreshRequest(BaseModel):
    source: Literal["auto", "cinemeta", "tvdb", "anilist"] = "auto"
    force: bool = False


@router.get("/titles/{title_id}/metadata/sources")
def title_metadata_sources(
    title_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    tvdb_configured = bool(request.app.state.settings.tvdb_api_key)
    sources = [
        {
            "id": "cinemeta",
            "label": "IMDb / Cinemeta",
            "available": bool(title.imdb_id),
            "reason": None if title.imdb_id else "NO_IMDB_ID",
        },
        {
            "id": "tvdb",
            "label": "TheTVDB",
            "available": bool(tvdb_configured and title.imdb_id),
            "reason": "NO_KEY"
            if not tvdb_configured
            else (None if title.imdb_id else "NO_IMDB_ID"),
        },
        {
            "id": "anilist",
            "label": "AniList",
            "available": title.kind == "anime",
            "reason": None if title.kind == "anime" else "NOT_ANIME",
        },
    ]
    selected = title.metadata_source
    return {"sources": sources, "selected": selected, "synced_at": title.metadata_synced_at}


@router.post("/titles/{title_id}/metadata/refresh")
async def refresh_title_metadata(
    title_id: str,
    body: RefreshRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    title = _require_title(session, title_id)
    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    return await refresh_title(
        session,
        title,
        client=client,
        cache=cache,
        settings=request.app.state.settings,
        source=body.source,
        force=body.force,
    )


@router.get("/metadata/anime/{anilist_id}")
async def anime_metadata_detail(
    request: Request,
    anilist_id: Annotated[int, PathParam(ge=1)],
) -> dict[str, Any]:
    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    detail, stale = await get_anime_detail(client, cache, anilist_id)
    payload = detail.to_dict()
    payload["stale"] = stale
    return payload


@router.get("/metadata/{kind}/{imdb_id}")
async def metadata_detail(
    request: Request,
    kind: MetaKind,
    imdb_id: Annotated[str, PathParam(pattern=r"^tt\d+$")],
) -> dict[str, Any]:
    cache: TTLCache = request.app.state.metadata_cache
    client: httpx.AsyncClient = request.app.state.http_client
    detail, stale = await get_detail(client, cache, kind, imdb_id)
    payload = detail.to_dict()
    payload["stale"] = stale
    return payload
