# T-003 — Metadata search: IMDb + Cinemeta

**Phase:** 1 · **Depends on:** T-002 · **Estimate:** M

## Goal

One search endpoint that turns free text into normalised, deduplicated title candidates with
an IMDb id, kind, year and artwork. IMDb suggestions is primary; Cinemeta is the fallback.

## Scope

- `services/metadata/imdb.py`: suggestion lookup with the first-character path segment,
  query normalisation, `qid`/`type` → `movie|series`.
- `services/metadata/cinemeta.py`: catalog search + `meta` fetch (details, episodes).
- `services/metadata/models.py`: internal `TitleCandidate`, `TitleDetail`, `EpisodeRef`.
- `services/metadata/cache.py`: TTL cache (1 h) with stale-on-error fallback.
- `routers/search.py`: `GET /api/search?q=&kind=&limit=`.
- `routers/titles.py`: `GET /api/metadata/{kind}/{imdb_id}` (detail prefetch, no DB write).

## Out of scope

Anime (T-004), persisting to the library (T-006), release search (T-007).

## Acceptance criteria

- [x] `GET /api/search?q=big buck bunny` returns results with
      `{source, source_id, kind, title, year, poster_url}`.
- [x] Queries with spaces, punctuation, apostrophes and non-ASCII are normalised correctly
      for the IMDb suggestion path.
- [x] If IMDb suggestions fail or return zero usable ids, Cinemeta catalog search is used.
- [x] Results are deduplicated by `(source_id, kind)` and capped by `limit` (default 20).
- [x] A 5 s timeout and 1 retry are applied; total request budget ≤ 8 s.
- [x] Stale cache is served, with `stale=true`, when both upstreams fail.
- [x] `GET /api/metadata/series/tt0944947` returns the episode list in `SxxEyy` ordering.
- [x] No network call is made in tests.

## Implementation notes

IMDb suggestion path:
`https://v2.sg.media-imdb.com/suggestion/{first}/{normalised}.json` where normalised is
lowercase, spaces → `_`, characters outside `[a-z0-9_.-]` → `_`, collapsed underscores.

Kind mapping: `feature|short|video|movie` → `movie`; `tv series|tv mini-series|tv movie|
tv special|series` → `series`. Unknown → `movie` but keep a `raw_type` field for debugging.

Cinemeta fallback:
`https://v3-cinemeta.strem.io/catalog/{kind}/top/search={urlencoded}.json`; read
`.metas[].id` (must be `tt\d+`), `.name`, `.releaseInfo`, `.poster`.

Detail: `https://v3-cinemeta.strem.io/meta/{kind}/{id}.json`; episodes from
`.meta.videos[]` where `id` is `tt...:season:episode`.

Use `httpx.AsyncClient` with a shared timeout and a `User-Agent: daku/<version>`.
Cache keys are the full URL; never cache non-2xx or invalid JSON.

## Test plan

- `tests/metadata/test_imdb.py`: recorded suggestion payloads incl. missing fields,
  bad ids, `qid` variants, normalisation edge cases.
- `tests/metadata/test_cinemeta.py`: catalog + meta fixtures, episode ordering.
- `tests/metadata/test_fallback.py`: IMDb 500 → Cinemeta used; both fail → stale cache.
- `tests/routers/test_search.py`: shape, limit, dedupe, empty query → 422.

## Completion notes

- Added `services/metadata/client.py` (shared `httpx.AsyncClient`, 5 s timeout, one retry,
  8 s budget, never caches non-2xx/invalid JSON) and `services/metadata/search.py`
  (IMDb → Cinemeta fallback plus stale-cache recovery).
- `GET /api/search` returns `{query, kind, stale, results[]}`; each result has exactly
  `source, source_id, kind, title, year, poster_url`.
- `GET /api/metadata/{kind}/{imdb_id}` returns the detail fields plus a `stale` flag and
  `SxxEyy` episode labels. `kind` is limited to `movie|series` until T-004 adds `anime`.
- Upstream failures with no cache return `502` with `SEARCH_UNAVAILABLE` /
  `METADATA_UNAVAILABLE`; the shared `AppError` handler was added in `app.py`.
- The metadata cache and HTTP client are created once in the FastAPI lifespan and disposed
  on shutdown.
