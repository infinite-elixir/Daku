"""Metadata refresh with user overrides applied (T-022 + T-029 + T-037)."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import replace
from typing import Any

import httpx
from sqlalchemy.orm import Session

from daku.config import Settings
from daku.errors import AppError
from daku.models import Title
from daku.repositories import cours as cours_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import seasons as seasons_repo
from daku.services.metadata import artwork, structure
from daku.services.metadata import cours as cours_service
from daku.services.metadata.aliases import build_generated_aliases, store_generated_aliases
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.models import EpisodeRef
from daku.services.metadata.overrides import (
    apply_episode_overrides,
    apply_overrides_to_detail_fields,
    apply_season_offsets_to_refs,
    load_season_offsets,
    overridden_fields,
    revert_episode_overrides,
)
from daku.services.metadata.resolve import enrich_anime_episode_names, resolve_candidate_detail
from daku.services.metadata.search import get_detail
from daku.utils import utc_now_iso


def _structure_source(chosen: str, detail: Any, title: Title) -> tuple[str, str]:
    if chosen == "anilist":
        return "anilist", str(title.anilist_id or detail.source_id)
    if chosen == "tvdb":
        return "tvdb", str(title.tvdb_id or detail.source_id)
    return "cinemeta", str(title.imdb_id or detail.source_id)


async def _refs_for_row(
    client: httpx.AsyncClient,
    cache: TTLCache,
    *,
    title: Title,
    row: Any,
    cinemeta_episodes: list[EpisodeRef] | None,
    tvdb_episodes: list[EpisodeRef] | None,
    deadline: float,
) -> list[EpisodeRef] | None:
    """Provider episodes for one season row. ``None`` means unreachable."""

    if row.source == "cinemeta":
        if cinemeta_episodes is None:
            return None
        return [ref for ref in cinemeta_episodes if ref.season == row.provider_season]

    if row.source == "tvdb":
        if tvdb_episodes is None:
            return None
        return [ref for ref in tvdb_episodes if ref.season == row.provider_season]

    if row.source == "anilist":
        anilist_id = row.anilist_id or title.anilist_id
        if not anilist_id:
            return None
        from daku.services.metadata.search import get_anime_detail

        try:
            detail, _stale = await get_anime_detail(client, cache, int(anilist_id))
        except AppError:
            return None
        detail.episodes = [
            EpisodeRef(season=1, episode=number)
            for number in range(1, (detail.episode_count or 0) + 1)
        ]
        await enrich_anime_episode_names(
            client,
            cache,
            detail,
            mal_id=detail.id_mal,
            deadline=deadline,
            title_id=title.id,
        )
        return [ref for ref in detail.episodes if ref.season == row.provider_season]

    return None


async def _refresh_structure_rows(
    client: httpx.AsyncClient,
    cache: TTLCache,
    session: Session,
    title: Title,
    rows: list[Any],
    *,
    offsets: dict[int, int],
    deadline: float,
) -> tuple[list[EpisodeRef], list[dict[str, Any]], int]:
    """Fetch each stored season, keeping unreachable seasons untouched."""

    cinemeta_episodes: list[EpisodeRef] | None = None
    tvdb_episodes: list[EpisodeRef] | None = None

    if any(row.source == "cinemeta" for row in rows) and title.imdb_id:
        try:
            detail, _stale = await get_detail(client, cache, "series", title.imdb_id)
        except AppError:
            detail = None
        if detail is not None:
            cinemeta_episodes = list(detail.episodes)
    if any(row.source == "tvdb" for row in rows) and (title.tvdb_id or title.imdb_id):
        fetched = await structure.tvdb_structure(
            client,
            cache,
            imdb_id=title.imdb_id,
            tvdb_id=title.tvdb_id,
            deadline=deadline,
        )
        if fetched is not None:
            tvdb_episodes = fetched.episodes

    refs: list[EpisodeRef] = []
    effective: dict[int, Any] = {}
    for row in rows:
        library_season = offsets.get(row.provider_season, row.library_season)
        effective.setdefault(library_season, row)
        provider_refs = await _refs_for_row(
            client,
            cache,
            title=title,
            row=row,
            cinemeta_episodes=cinemeta_episodes,
            tvdb_episodes=tvdb_episodes,
            deadline=deadline,
        )
        if not provider_refs:
            continue
        refs.extend(replace(ref, season=library_season) for ref in provider_refs)

    if not refs:
        return [], [], 0

    counts = Counter(ref.season for ref in refs)
    existing_counts = Counter(row.season for row in episodes_repo.list_episodes(session, title.id))
    library_seasons = sorted(set(effective) | set(counts))
    cumulative: dict[int, int] = {}
    running = 0
    for library in library_seasons:
        if library == 0:
            cumulative[library] = 0
            continue
        cumulative[library] = running
        running += counts.get(library, existing_counts.get(library, 0))

    specs: list[dict[str, Any]] = []
    for library in sorted(effective):
        row = effective[library]
        specs.append(
            {
                "library_season": library,
                "provider_season": row.provider_season,
                "source": row.source,
                "source_id": row.source_id,
                "anilist_id": row.anilist_id,
                "absolute_offset": cumulative.get(library, 0),
                "episode_count": counts.get(library, existing_counts.get(library, 0)),
            }
        )
    refs.sort(key=lambda ref: (ref.season, ref.episode))
    return refs, specs, len(refs)


async def refresh_title(
    session: Session,
    title: Title,
    *,
    client: httpx.AsyncClient,
    cache: TTLCache,
    settings: Settings,
    source: str = "auto",
    force: bool = False,
    renumber: bool = False,
) -> dict[str, object]:
    """Fetch provider metadata, then upsert without touching overridden fields.

    ``renumber`` rewrites the existing episode rows to the provider's numbering in
    positional order. It is used when a series becomes anime, so episode ids (and the
    releases, downloads and files attached to them) survive the structural change.
    """

    del force  # Reserved for a future forced provider overwrite; overrides always win.
    imdb_id = title.imdb_id
    kind = title.kind
    is_anime = kind == "anime"

    chosen: str
    if source != "auto":
        chosen = source
    elif title.metadata_source:
        chosen = title.metadata_source
    elif is_anime:
        chosen = "anilist"
    else:
        chosen = settings.metadata_sources[0] if settings.metadata_sources else "cinemeta"

    def validate(candidate_source: str) -> str:
        if candidate_source == "anilist":
            if not is_anime:
                raise AppError(
                    code="NOT_ANIME",
                    message="AniList is only available for anime titles",
                    status_code=422,
                )
            if not title.anilist_id:
                raise AppError(
                    code="NO_ANILIST_ID",
                    message="This title has no AniList id",
                    status_code=422,
                )
            return str(title.anilist_id)
        if not imdb_id:
            raise AppError(
                code="NO_IMDB_ID",
                message="This title has no IMDb id",
                status_code=422,
            )
        return imdb_id

    source_id = validate(chosen)
    try:
        detail, stale = await resolve_candidate_detail(
            client, cache, source=chosen, source_id=source_id, kind=kind
        )
    except AppError:
        if source != "auto":
            raise
        for fallback in settings.metadata_sources:
            if fallback == chosen:
                continue
            try:
                candidate_id = validate(fallback)
                detail, stale = await resolve_candidate_detail(
                    client, cache, source=fallback, source_id=candidate_id, kind=kind
                )
                chosen = fallback
                break
            except AppError:
                continue
        else:
            raise

    deadline = time.monotonic() + 8.0
    offsets = load_season_offsets(session, title.id)
    season_rows = seasons_repo.list_seasons(session, title.id)

    if is_anime:
        tvdb_names: list[EpisodeRef] | None = None
        if title.tvdb_id:
            tvdb_fetch = await structure.tvdb_structure(
                client,
                cache,
                imdb_id=title.imdb_id,
                tvdb_id=title.tvdb_id,
                deadline=deadline,
            )
            if tvdb_fetch is not None:
                tvdb_names = tvdb_fetch.episodes
        await enrich_anime_episode_names(
            client,
            cache,
            detail,
            mal_id=title.mal_id or detail.id_mal,
            tvdb_refs=tvdb_names,
            deadline=deadline,
            title_id=title.id,
        )

    specs: list[dict[str, Any]] = []
    existing_cours = cours_repo.list_cours(session, title.id) if is_anime else []
    split_episodes: list[EpisodeRef] | None = None
    cour_ranges: list[cours_service.CourRange] = []
    if is_anime and existing_cours:
        if existing_cours[0].anilist_id == title.anilist_id:
            entries = await cours_service.stored_cours(
                client, cache, rows=existing_cours, deadline=deadline
            )
            ranges = cours_service.ranges_from_stored(existing_cours, entries)
            cour_ranges = ranges
            refreshed_rows = cours_service.cour_rows(ranges)
            if any(
                (row.library_season, row.start_episode, row.end_episode, row.anilist_id)
                != (
                    new["library_season"],
                    new["start_episode"],
                    new["end_episode"],
                    new["anilist_id"],
                )
                for row, new in zip(existing_cours, refreshed_rows, strict=False)
            ):
                cours_repo.replace_cours(session, title.id, refreshed_rows)
            split_episodes = cours_service.combined_refs(ranges)
            specs = cours_service.season_specs(ranges)
        else:
            # The primary identity changed; old cours no longer describe it.
            cours_repo.delete_cours(session, title.id)
            existing_cours = []

    if is_anime and not existing_cours and not season_rows and title.anilist_id:
        entries = await cours_service.resolve_cours(
            client,
            cache,
            primary_id=int(title.anilist_id),
            primary=detail,
            deadline=deadline,
        )
        if len(entries) > 1:
            provider = cours_service.provider_season_for_anilist(
                session, [entry.anilist_id for entry in entries]
            )
            ranges = cours_service.build_ranges(entries, provider_season=provider)
            cour_ranges = ranges
            cours_repo.replace_cours(session, title.id, cours_service.cour_rows(ranges))
            split_episodes = cours_service.combined_refs(ranges)
            specs = cours_service.season_specs(ranges)

    if split_episodes is not None:
        episodes = split_episodes
    elif season_rows and kind != "movie":
        episodes, specs, _fetched = await _refresh_structure_rows(
            client,
            cache,
            session,
            title,
            season_rows,
            offsets=offsets,
            deadline=deadline,
        )
        if not episodes:
            # Every provider is unreachable: keep the existing structure intact.
            episodes = []
    else:
        source_name, structure_id = _structure_source(chosen, detail, title)
        season_specs = structure.season_specs(
            session,
            episodes=list(detail.episodes),
            source=source_name,
            source_id=structure_id,
            default_anilist_id=title.anilist_id,
            offsets=offsets,
        )
        episodes = structure.library_refs(list(detail.episodes), season_specs)
        if is_anime:
            specs = [
                {
                    "library_season": spec.library_season,
                    "provider_season": spec.provider_season,
                    "source": spec.source,
                    "source_id": spec.source_id,
                    "anilist_id": spec.anilist_id,
                    "absolute_offset": spec.absolute_offset,
                    "episode_count": spec.episode_count,
                }
                for spec in season_specs
            ]
        else:
            episodes = apply_season_offsets_to_refs(list(detail.episodes), offsets)

    season_offsets = (
        {spec["library_season"]: spec["absolute_offset"] for spec in specs} if is_anime else None
    )

    before_keys = {
        (row.season, row.episode) for row in episodes_repo.list_episodes(session, title.id)
    }
    # Overrides may have moved rows; put them back under provider coordinates first.
    revert_episode_overrides(session, title.id)
    if renumber and episodes:
        existing = sorted(
            episodes_repo.list_episodes(session, title.id),
            key=lambda row: (row.season, row.episode),
        )
        for row, ref in zip(existing, episodes, strict=False):
            row.season = ref.season
            row.episode = ref.episode
            row.absolute = (
                None
                if not is_anime
                else (
                    None
                    if ref.season == 0
                    else (season_offsets or {}).get(ref.season, 0) + ref.episode
                )
            )
            if ref.title is not None:
                row.name = ref.title
            if ref.air_date is not None:
                row.air_date = ref.air_date
        session.flush()
    if episodes:
        episodes_repo.sync_episodes(
            session,
            title_id=title.id,
            episodes=episodes,
            absolute_from_number=is_anime,
            season_offsets=season_offsets,
        )
    if is_anime and specs:
        seasons_repo.upsert_seasons(session, title.id, specs)
    apply_episode_overrides(session, title.id)
    after_keys = {
        (row.season, row.episode) for row in episodes_repo.list_episodes(session, title.id)
    }
    added = len(after_keys - before_keys)
    updated = max(0, len(episodes) - added)

    poster_candidates: list[str | None] = [detail.poster_url]
    if is_anime:
        poster_candidates.extend(item.entry.poster_url for item in cour_ranges)
    poster: str | None = None
    seen_posters: set[str] = set()
    for candidate in poster_candidates:
        if not candidate or candidate in seen_posters:
            continue
        seen_posters.add(candidate)
        if await artwork.artwork_ok(client, candidate):
            poster = candidate
            break

    stored_poster = title.poster_url
    if not poster and stored_poster and await artwork.artwork_ok(client, stored_poster):
        poster = stored_poster

    banner_candidates: list[str | None] = [detail.banner_url]
    if is_anime:
        banner_candidates.extend(item.entry.banner_url for item in cour_ranges)
    banner: str | None = None
    seen_banners: set[str] = set()
    for candidate in banner_candidates:
        if not candidate or candidate in seen_banners:
            continue
        seen_banners.add(candidate)
        if await artwork.artwork_ok(client, candidate):
            banner = candidate
            break

    stored_banner = title.banner_url
    if not banner and stored_banner and await artwork.artwork_ok(client, stored_banner):
        banner = stored_banner

    updates: dict[str, object] = {}
    if detail.title and detail.title.strip():
        updates["title"] = detail.title.strip()
    if detail.year is not None:
        updates["year"] = detail.year
    if detail.overview and detail.overview.strip():
        updates["overview"] = detail.overview.strip()
    if poster:
        updates["poster_url"] = poster
    elif stored_poster:
        # A dead or unverifiable URL must not survive as if it were artwork.
        updates["poster_url"] = None
    if banner:
        updates["banner_url"] = banner
    elif stored_banner:
        updates["banner_url"] = None
    if source != "auto":
        updates["metadata_source"] = source
    updates["metadata_synced_at"] = utc_now_iso()
    updates = apply_overrides_to_detail_fields(session, title, updates)

    for field, value in updates.items():
        setattr(title, field, value)
    alias_details = [detail, *(item.entry.detail for item in cour_ranges)]
    store_generated_aliases(
        session,
        title,
        build_generated_aliases(str(updates.get("title") or title.title), alias_details),
    )
    session.commit()

    artwork_missing = not (title.poster_url or "")
    refresh_error = (
        {
            "code": "ARTWORK_UNAVAILABLE",
            "message": "No usable poster artwork was found for this title",
        }
        if artwork_missing
        else None
    )
    return {
        "title_id": title.id,
        "source": chosen,
        "partial": artwork_missing,
        "refresh_error": refresh_error,
        "report": {
            "added": added,
            "updated": updated,
            "skipped": 0,
            "stale": stale,
        },
        "overridden_fields": overridden_fields(session, title.id),
    }
