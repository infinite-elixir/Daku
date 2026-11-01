"""AniList GraphQL adapter for anime search and detail.

AniList is the primary anime metadata source (T-004). A Jikan fallback was deferred:
AniList covers search, formats, episode counts and relations on its own.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import (
    UpstreamError,
    cached_payload,
    post_json,
    request_cache_key,
)
from daku.services.metadata.models import TitleCandidate, TitleDetail

ENDPOINT = "https://graphql.anilist.co"
MAX_PER_PAGE = 50
RELATION_TYPES = ("SEQUEL", "PREQUEL", "SIDE_STORY")

_SEARCH_QUERY = """
query Search($search: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      idMal
      format
      episodes
      seasonYear
      countryOfOrigin
      startDate { year }
      title { romaji english native }
      synonyms
      coverImage { extraLarge color }
      description(asHtml: false)
    }
  }
}
"""

_DETAIL_QUERY = """
query Detail($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    format
    episodes
    duration
    status
    season
    seasonYear
    startDate { year month day }
    title { romaji english native }
    synonyms
    coverImage { extraLarge color }
    bannerImage
    description(asHtml: false)
    genres
    studios(isMain: true) { nodes { name } }
    streamingEpisodes { title site }
    relations {
      edges {
        relationType
        node {
          id
          idMal
          type
          format
          episodes
          seasonYear
          countryOfOrigin
          startDate { year }
          title { romaji english native }
          coverImage { extraLarge }
          bannerImage
        }
      }
    }
  }
}
"""

PROBE_QUERY = """
query Probe($id: Int) {
  Media(id: $id) {
    id
    idMal
    type
    format
    episodes
    seasonYear
    title { romaji english native }
  }
}
"""

PROBE_MAL_QUERY = """
query ProbeMal($idMal: Int) {
  Media(idMal: $idMal) {
    id
    idMal
    type
    format
    episodes
    seasonYear
    title { romaji english native }
  }
}
"""

_BREAK_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_STREAMING_TITLE_RE = re.compile(
    r"^\s*(?:episode|ep\.?)\s*(\d+)\s*(?:[\-\u2013\u2014:.]\s*)?(.*)$",
    re.IGNORECASE,
)


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _year(item: dict[str, Any]) -> int | None:
    year = _as_int(item.get("seasonYear"))
    if year is not None:
        return year
    start_date = item.get("startDate")
    if isinstance(start_date, dict):
        return _as_int(start_date.get("year"))
    return None


def strip_html(value: Any) -> str | None:
    """Turn an AniList description into plain text. Never return raw HTML."""

    raw = _text(value)
    if raw is None:
        return None
    without_breaks = _BREAK_RE.sub("\n", raw)
    without_tags = _TAG_RE.sub("", without_breaks)
    cleaned = html.unescape(without_tags)
    lines = [line.strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line).strip() or None


def _title(item: dict[str, Any]) -> str | None:
    title = item.get("title")
    if isinstance(title, dict):
        for key in ("english", "romaji", "native"):
            value = _text(title.get(key))
            if value:
                return value
    return None


def _title_variants(item: dict[str, Any]) -> tuple[str, ...]:
    """Every title spelling AniList knows, used for conservative matching."""

    values: list[str] = []
    title = item.get("title")
    if isinstance(title, dict):
        for key in ("english", "romaji", "native"):
            value = _text(title.get(key))
            if value:
                values.append(value)
    for synonym in item.get("synonyms") or []:
        if isinstance(synonym, str) and synonym.strip():
            values.append(synonym.strip())
    return tuple(dict.fromkeys(values))


def _poster(item: dict[str, Any]) -> str | None:
    cover = item.get("coverImage")
    if isinstance(cover, dict):
        return _text(cover.get("extraLarge"))
    return None


def _graphql_data(payload: Any, cache: TTLCache, key: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and data is None:
        cache.delete(key)
        message = "unknown error"
        first = errors[0]
        if isinstance(first, dict):
            message = _text(first.get("message")) or message
        raise UpstreamError(ENDPOINT, f"GraphQL error: {message}")
    return data


def parse_search(payload: Any) -> list[TitleCandidate]:
    """Parse a ``Page.media`` payload. Missing fields become ``None``."""

    if not isinstance(payload, dict):
        return []
    page = payload.get("Page")
    if not isinstance(page, dict):
        return []
    media = page.get("media")
    if not isinstance(media, list):
        return []

    candidates: list[TitleCandidate] = []
    for item in media:
        if not isinstance(item, dict):
            continue
        source_id = _as_int(item.get("id"))
        title = _title(item)
        if source_id is None or title is None:
            continue
        fmt = _text(item.get("format"))
        variants = _title_variants(item)
        candidates.append(
            TitleCandidate(
                source="anilist",
                source_id=str(source_id),
                kind="anime",
                title=title,
                year=_year(item),
                poster_url=_poster(item),
                raw_type=fmt,
                id_mal=_as_int(item.get("idMal")),
                format=fmt,
                episode_count=_as_int(item.get("episodes")),
                country_of_origin=_text(item.get("countryOfOrigin")),
                synonyms=variants,
            )
        )
    return candidates


def _related(node: dict[str, Any]) -> dict[str, Any] | None:
    source_id = _as_int(node.get("id"))
    title = _title(node)
    if source_id is None or title is None:
        return None
    return {
        "source": "anilist",
        "source_id": str(source_id),
        "title": title,
        "year": _year(node),
        "type": _text(node.get("type")),
        "format": _text(node.get("format")),
        "episode_count": _as_int(node.get("episodes")),
        "id_mal": _as_int(node.get("idMal")),
        "country_of_origin": _text(node.get("countryOfOrigin")),
        "poster_url": _poster(node),
        "banner_url": _text(node.get("bannerImage")),
    }


def _parse_relations(media: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {relation: [] for relation in RELATION_TYPES}
    relations = media.get("relations")
    if not isinstance(relations, dict):
        return grouped
    edges = relations.get("edges")
    if not isinstance(edges, list):
        return grouped

    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        relation_type = _text(edge.get("relationType"))
        node = edge.get("node")
        if relation_type is None or not isinstance(node, dict):
            continue
        parsed = _related(node)
        if parsed is None:
            continue
        key = (parsed["source_id"], relation_type)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(relation_type, []).append(parsed)
    return grouped


def parse_streaming_episodes(media: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn ``streamingEpisodes`` into ``[{episode, title}]``.

    Titles look like ``Episode 3 - The Journey``. Anything without a leading
    episode number is ignored because it could not be matched safely.
    """

    raw = media.get("streamingEpisodes")
    if not isinstance(raw, list):
        return []
    parsed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_title = _text(item.get("title"))
        if raw_title is None:
            continue
        match = _STREAMING_TITLE_RE.match(raw_title)
        if match is None:
            continue
        number = int(match.group(1))
        if number < 1 or number in seen:
            continue
        seen.add(number)
        title = match.group(2).strip()
        parsed.append({"episode": number, "title": title or None})
    return parsed


def parse_detail(payload: Any) -> TitleDetail | None:
    """Parse a ``Media`` payload, including one level of relations."""

    if not isinstance(payload, dict):
        return None
    media = payload.get("Media")
    if not isinstance(media, dict):
        return None

    source_id = _as_int(media.get("id"))
    title = _title(media)
    if source_id is None or title is None:
        return None

    studios: list[str] = []
    raw_studios = media.get("studios")
    if isinstance(raw_studios, dict) and isinstance(raw_studios.get("nodes"), list):
        for node in raw_studios["nodes"]:
            if isinstance(node, dict):
                name = _text(node.get("name"))
                if name:
                    studios.append(name)

    genres = [
        genre.strip()
        for genre in media.get("genres") or []
        if isinstance(genre, str) and genre.strip()
    ]
    fmt = _text(media.get("format"))
    duration = _as_int(media.get("duration"))
    # AniList's ``duration`` is minutes per episode for TV; only a movie's is a runtime.
    runtime_minutes = duration if fmt == "MOVIE" and duration and duration > 0 else None
    synonyms = [
        synonym.strip()
        for synonym in media.get("synonyms") or []
        if isinstance(synonym, str) and synonym.strip()
    ]

    return TitleDetail(
        source="anilist",
        source_id=str(source_id),
        kind="anime",
        title=title,
        year=_year(media),
        poster_url=_poster(media),
        overview=strip_html(media.get("description")),
        raw_type=_text(media.get("format")),
        id_mal=_as_int(media.get("idMal")),
        format=_text(media.get("format")),
        episode_count=_as_int(media.get("episodes")),
        runtime_minutes=runtime_minutes,
        banner_url=_text(media.get("bannerImage")),
        studios=studios,
        genres=genres,
        status=_text(media.get("status")),
        season=_text(media.get("season")),
        season_year=_as_int(media.get("seasonYear")),
        synonyms=synonyms,
        aliases=list(_title_variants(media)),
        relations=_parse_relations(media),
        streaming_episodes=parse_streaming_episodes(media),
    )


def search_body(query: str, limit: int) -> dict[str, Any]:
    return {
        "query": _SEARCH_QUERY,
        "variables": {"search": query, "page": 1, "perPage": max(1, min(limit, MAX_PER_PAGE))},
    }


def detail_body(anilist_id: int) -> dict[str, Any]:
    return {"query": _DETAIL_QUERY, "variables": {"id": anilist_id}}


def probe_body(anilist_id: int) -> dict[str, Any]:
    return {"query": PROBE_QUERY, "variables": {"id": anilist_id}}


def probe_mal_body(mal_id: int) -> dict[str, Any]:
    return {"query": PROBE_MAL_QUERY, "variables": {"idMal": mal_id}}


def parse_probe(payload: Any) -> dict[str, Any] | None:
    """Return the raw type/format row even when it is not an anime."""

    if not isinstance(payload, dict):
        return None
    media = payload.get("Media")
    if not isinstance(media, dict):
        return None
    return media


async def probe_media(
    client: httpx.AsyncClient,
    cache: TTLCache,
    anilist_id: int,
    *,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    """Look up an AniList id without the ``type: ANIME`` filter.

    ``fetch_detail`` deliberately asks for ``type: ANIME``, so a manga id looks
    exactly like a missing entry. This probe tells the two apart for validation.
    """

    body = probe_body(anilist_id)
    key = request_cache_key(ENDPOINT, body)
    payload = await post_json(client, ENDPOINT, body, cache, deadline=deadline, cache_key=key)
    data = _graphql_data(payload, cache, key)
    return parse_probe(data)


async def probe_media_by_mal(
    client: httpx.AsyncClient,
    cache: TTLCache,
    mal_id: int,
    *,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    """Resolve a MyAnimeList id to its AniList row, for manual-id suggestions."""

    body = probe_mal_body(mal_id)
    key = request_cache_key(ENDPOINT, body)
    payload = await post_json(client, ENDPOINT, body, cache, deadline=deadline, cache_key=key)
    data = _graphql_data(payload, cache, key)
    return parse_probe(data)


async def search(
    client: httpx.AsyncClient,
    cache: TTLCache,
    query: str,
    *,
    limit: int = 20,
    deadline: float | None = None,
) -> list[TitleCandidate]:
    body = search_body(query, limit)
    key = request_cache_key(ENDPOINT, body)
    payload = await post_json(client, ENDPOINT, body, cache, deadline=deadline, cache_key=key)
    return parse_search(_graphql_data(payload, cache, key))


def cached_search(cache: TTLCache, query: str, *, limit: int = 20) -> list[TitleCandidate]:
    body = search_body(query, limit)
    payload = cached_payload(cache, request_cache_key(ENDPOINT, body))
    if payload is None:
        return []
    return parse_search(payload.get("data") if isinstance(payload, dict) else None)


async def fetch_detail(
    client: httpx.AsyncClient,
    cache: TTLCache,
    anilist_id: int,
    *,
    deadline: float | None = None,
) -> TitleDetail | None:
    body = detail_body(anilist_id)
    key = request_cache_key(ENDPOINT, body)
    payload = await post_json(client, ENDPOINT, body, cache, deadline=deadline, cache_key=key)
    return parse_detail(_graphql_data(payload, cache, key))


def cached_detail(cache: TTLCache, anilist_id: int) -> TitleDetail | None:
    payload = cached_payload(cache, request_cache_key(ENDPOINT, detail_body(anilist_id)))
    if payload is None:
        return None
    return parse_detail(payload.get("data") if isinstance(payload, dict) else None)
