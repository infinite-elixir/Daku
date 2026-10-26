"""Search orchestration: IMDb first, Cinemeta fallback, stale cache last.

Anime search is routed to AniList; it never touches the IMDb/Cinemeta path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from daku.errors import AppError
from daku.services.metadata import anilist, cinemeta, imdb
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.classification import (
    enrich_exact,
    mapped_anilist_ids,
    mapping_dataset_present,
)
from daku.services.metadata.client import (
    REQUEST_BUDGET,
    UpstreamError,
    cached_payload,
    fetch_json,
)
from daku.services.metadata.models import TitleCandidate, TitleDetail

SEARCH_KINDS = ("movie", "series", "anime")
DEFAULT_LIMIT = 20
ANIME_FORMATS = ("TV", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SHORT", "MUSIC")


@dataclass(slots=True)
class SearchOutcome:
    results: list[TitleCandidate]
    stale: bool = False


def dedupe_candidates(candidates: list[TitleCandidate], limit: int) -> list[TitleCandidate]:
    seen: set[tuple[str, str]] = set()
    results: list[TitleCandidate] = []
    for candidate in candidates:
        key = (candidate.source_id, candidate.kind)
        if key in seen:
            continue
        seen.add(key)
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


def _catalog_kinds(kind: str | None) -> list[str]:
    if kind is None:
        return ["movie", "series"]
    return [kind]


def filter_by_format(candidates: list[TitleCandidate], fmt: str | None) -> list[TitleCandidate]:
    if fmt is None:
        return candidates
    wanted = fmt.upper()
    return [
        candidate
        for candidate in candidates
        if candidate.format is not None and candidate.format.upper() == wanted
    ]


def _apply_mapping(
    session: Session | None,
    candidates: list[TitleCandidate],
) -> int:
    """Batch-enrich IMDb/Cinemeta candidates from AniBridge. One query per search."""

    if session is None:
        return 0
    series = [candidate for candidate in candidates if candidate.kind == "series"]
    if not series:
        return 0
    mapped = mapped_anilist_ids(session, [candidate.source_id for candidate in series])
    count = 0
    for candidate in series:
        anilist_id = mapped.get(candidate.source_id)
        if anilist_id is None or candidate.anime:
            continue
        candidate.anime = True
        candidate.anilist_id = anilist_id
        candidate.classification_reason = "Detected via AniBridge mapping"
        count += 1
    return count


async def _enrich_candidates(
    client: httpx.AsyncClient,
    cache: TTLCache,
    query: str,
    candidates: list[TitleCandidate],
    *,
    kind: str | None,
    deadline: float,
    session: Session | None,
) -> None:
    """Mapping first; one conservative AniList exact-match call for the rest."""

    _apply_mapping(session, candidates)
    if kind == "movie" or session is None or not mapping_dataset_present(session):
        return
    unmapped = [c for c in candidates if c.kind == "series" and not c.anime]
    if not unmapped:
        return
    try:
        media = await anilist.search(client, cache, query, limit=10, deadline=deadline)
    except UpstreamError:
        media = anilist.cached_search(cache, query, limit=10)
    if media:
        enrich_exact(unmapped, media)


async def _mapped_imdb_candidates(
    client: httpx.AsyncClient,
    cache: TTLCache,
    query: str,
    session: Session,
    *,
    deadline: float,
) -> list[TitleCandidate]:
    """IMDb rows that AniBridge maps to AniList, for merging into the Anime tab."""

    try:
        payload = await fetch_json(
            client, imdb.build_suggestion_url(query), cache, deadline=deadline
        )
    except UpstreamError:
        payload = cached_payload(cache, imdb.build_suggestion_url(query))
        if payload is None:
            return []
    suggestions = [c for c in imdb.parse_suggestions(payload) if c.kind == "series"]
    mapped = mapped_anilist_ids(session, [c.source_id for c in suggestions])
    results: list[TitleCandidate] = []
    for candidate in suggestions:
        anilist_id = mapped.get(candidate.source_id)
        if anilist_id is None:
            continue
        candidate.anime = True
        candidate.anilist_id = anilist_id
        candidate.classification_reason = "Detected via AniBridge mapping"
        results.append(candidate)
    return results


async def _search_anime(
    client: httpx.AsyncClient,
    cache: TTLCache,
    query: str,
    *,
    fmt: str | None,
    limit: int,
    deadline: float,
    session: Session | None = None,
) -> SearchOutcome:
    # Fetch a full page so a format filter still has candidates to choose from.
    fetch_limit = max(limit, 20)
    try:
        candidates = await anilist.search(
            client, cache, query, limit=fetch_limit, deadline=deadline
        )
    except UpstreamError:
        stale = anilist.cached_search(cache, query, limit=fetch_limit)
        stale = filter_by_format(stale, fmt)
        if stale:
            return SearchOutcome(dedupe_candidates(stale, limit), stale=True)
        raise AppError(
            code="SEARCH_UNAVAILABLE",
            message="Anime metadata search is temporarily unavailable",
            status_code=502,
        ) from None

    if session is not None and mapping_dataset_present(session):
        known = {candidate.anilist_id for candidate in candidates if candidate.anilist_id}
        for candidate in await _mapped_imdb_candidates(
            client, cache, query, session, deadline=deadline
        ):
            if candidate.anilist_id is not None and candidate.anilist_id in known:
                continue
            if candidate.anilist_id is not None:
                known.add(candidate.anilist_id)
            candidates.append(candidate)

    return SearchOutcome(
        dedupe_candidates(filter_by_format(candidates, fmt), limit),
        stale=False,
    )


async def search_titles(
    client: httpx.AsyncClient,
    cache: TTLCache,
    query: str,
    *,
    kind: str | None = None,
    limit: int = DEFAULT_LIMIT,
    fmt: str | None = None,
    session: Session | None = None,
) -> SearchOutcome:
    deadline = time.monotonic() + REQUEST_BUDGET

    if kind == "anime":
        return await _search_anime(
            client, cache, query, fmt=fmt, limit=limit, deadline=deadline, session=session
        )

    imdb_url = imdb.build_suggestion_url(query)
    catalog_urls = [
        (catalog_kind, cinemeta.catalog_url(catalog_kind, query))
        for catalog_kind in _catalog_kinds(kind)
    ]

    imdb_candidates: list[TitleCandidate] = []
    try:
        payload = await fetch_json(client, imdb_url, cache, deadline=deadline)
    except UpstreamError:
        pass
    else:
        imdb_candidates = imdb.parse_suggestions(payload)
        matching = [
            candidate for candidate in imdb_candidates if kind is None or candidate.kind == kind
        ]
        if matching:
            results = dedupe_candidates(matching, limit)
            await _enrich_candidates(
                client, cache, query, results, kind=kind, deadline=deadline, session=session
            )
            return SearchOutcome(results, stale=False)

    catalog_results: list[TitleCandidate] = []
    any_catalog_ok = False
    for catalog_kind, url in catalog_urls:
        try:
            payload = await fetch_json(client, url, cache, deadline=deadline)
        except UpstreamError:
            continue
        any_catalog_ok = True
        catalog_results.extend(cinemeta.parse_catalog(payload, catalog_kind))

    if any_catalog_ok and catalog_results:
        results = dedupe_candidates(catalog_results, limit)
        await _enrich_candidates(
            client, cache, query, results, kind=kind, deadline=deadline, session=session
        )
        return SearchOutcome(results, stale=False)

    stale_results: list[TitleCandidate] = []
    imdb_cached = cached_payload(cache, imdb_url)
    if imdb_cached is not None:
        stale_results.extend(
            candidate
            for candidate in imdb.parse_suggestions(imdb_cached)
            if kind is None or candidate.kind == kind
        )
    for catalog_kind, url in catalog_urls:
        cached = cached_payload(cache, url)
        if cached is not None:
            stale_results.extend(cinemeta.parse_catalog(cached, catalog_kind))

    if stale_results:
        return SearchOutcome(dedupe_candidates(stale_results, limit), stale=True)

    if any_catalog_ok:
        return SearchOutcome([], stale=False)

    raise AppError(
        code="SEARCH_UNAVAILABLE",
        message="Metadata search is temporarily unavailable",
        status_code=502,
    )


async def get_detail(
    client: httpx.AsyncClient,
    cache: TTLCache,
    kind: str,
    imdb_id: str,
) -> tuple[TitleDetail, bool]:
    url = cinemeta.meta_url(kind, imdb_id)
    deadline = time.monotonic() + REQUEST_BUDGET
    try:
        payload = await fetch_json(client, url, cache, deadline=deadline)
    except UpstreamError:
        payload = None

    if payload is not None:
        detail = cinemeta.parse_meta(payload, kind, imdb_id)
        if detail is not None:
            return detail, False

    stale_payload = cached_payload(cache, url)
    if stale_payload is not None:
        stale_detail = cinemeta.parse_meta(stale_payload, kind, imdb_id)
        if stale_detail is not None:
            return stale_detail, True

    raise AppError(
        code="METADATA_UNAVAILABLE",
        message=f"Metadata for {imdb_id} is temporarily unavailable",
        status_code=502,
    )


async def get_anime_detail(
    client: httpx.AsyncClient,
    cache: TTLCache,
    anilist_id: int,
) -> tuple[TitleDetail, bool]:
    deadline = time.monotonic() + REQUEST_BUDGET
    try:
        detail = await anilist.fetch_detail(client, cache, anilist_id, deadline=deadline)
    except UpstreamError:
        detail = None

    if detail is not None:
        return detail, False

    stale_detail = anilist.cached_detail(cache, anilist_id)
    if stale_detail is not None:
        return stale_detail, True

    raise AppError(
        code="METADATA_UNAVAILABLE",
        message=f"Anime metadata for {anilist_id} is temporarily unavailable",
        status_code=502,
    )
