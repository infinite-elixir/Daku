# T-004 — Anime metadata: AniList

**Phase:** 1 · **Depends on:** T-003 · **Estimate:** M

> **Scope change (2026-10-23):** Jikan was dropped from this ticket. AniList alone
> covers search, formats, episode counts and relations, so there is no second adapter
> to keep in sync for v1. The Jikan acceptance criteria are recorded as deferred below.

## Goal

Anime search and detail that is good enough to pick a specific cour and episode, using
AniList GraphQL.

## Scope

- `services/metadata/anilist.py`: GraphQL queries for `Page.media(search:)` and
  `Media(id:)` with title, synonyms, format, episodes, duration, status, season,
  start date, cover, banner, studios, genres, `idMal`, `relations`.
- `services/metadata/anime.py` normaliser is folded into `anilist.py` + the shared
  models; results use the same `TitleCandidate` / `TitleDetail` shapes as T-003 with
  `kind="anime"` and the extra anime fields.
- Extend `GET /api/search` with `kind=anime` and an optional `format` filter.
- Add `GET /api/metadata/anime/{anilist_id}`.

## Out of scope

IMDb↔AniList mapping (T-005), Torrentio (T-007), Jellyfin naming (T-013).

## Acceptance criteria

- [x] `GET /api/search?q=frieren&kind=anime` returns AniList results with `source_id`
      as the AniList id, `id_mal` when present, format, episode count and year.
- [x] Anime results are displayed with format (`TV`, `MOVIE`, `OVA`, `ONA`, `SPECIAL`)
      and are filterable by it in the response (`format=`).
- [ ] If AniList fails or is rate-limited, Jikan is used and the source is marked `jikan`.
      — **deferred**, Jikan is out of scope per the scope change above.
- [ ] Jikan requests never exceed 3/s; a 429 triggers backoff and one retry.
      — **deferred**, no Jikan adapter.
- [x] `GET /api/metadata/anime/{id}` returns seasons/cours grouped by relation type
      (`SEQUEL`, `PREQUEL`, `SIDE_STORY`) without recursing more than one level.
- [x] The adapter returns `None` for missing fields rather than raising.
- [x] Tests use fixtures; no live calls.

## Implementation notes

AniList endpoint: `POST https://graphql.anilist.co` with a JSON body. Ask for
`Page(perPage: 20) { media(search: $q, type: ANIME, isAdult: false) { ... } }`.
Relevant fields: `id, idMal, title{romaji english native}, synonyms, format, episodes,
duration, status, seasonYear, startDate{year month day}, coverImage{extraLarge color},
bannerImage, description(asHtml: false), genres, studios(isMain: true){nodes{name}}`.

Course handling: a "season" for anime is not AniList's concept. Keep the AniList entry as
the unit, and let T-005 map it onto a TVDB/TMDB season. Do not invent seasons here.

GraphQL error payloads (`data: null` + `errors[]`) are treated as an upstream failure and
are evicted from the cache. Descriptions are HTML-stripped before leaving the adapter.

AniList caps `perPage` at 50; the adapter clamps the requested limit. Format filtering is
client-side over a full page so a filtered query still has candidates.

## Test plan

- `tests/metadata/test_anilist.py`: normal result, missing `idMal`, movie format,
  GraphQL error payload, HTML description stripping, one-level relation grouping.
- `tests/routers/test_search_anime.py`: `kind=anime` routing, shape parity with T-003,
  format filter, stale cache, detail relations, 502 paths.

## Completion notes

- `services/metadata/client.py` gained `post_json` with body-hashed cache keys, so every
  GraphQL operation gets its own cache entry under the shared endpoint URL.
- `TitleCandidate` carries `id_mal`, `format` and `episode_count`; `TitleDetail` adds
  banner, studios, genres, status, season and relations. Both only expose the anime
  fields when `kind == "anime"`, so the T-003 movie/series contract is unchanged.
- `GET /api/search` accepts `kind=anime`; `format=` is validated as a literal and
  rejected (422) unless `kind=anime`.
- `GET /api/metadata/anime/{id}` is declared before the `{kind}` route so `/anime/123`
  is not captured by the IMDb detail handler.
- Anime search falls back to fresh/stale AniList cache; with no cache it raises
  `SEARCH_UNAVAILABLE` (502). Detail raises `METADATA_UNAVAILABLE` (502) the same way.
