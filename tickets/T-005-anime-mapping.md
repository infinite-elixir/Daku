# T-005 — Anime ID and episode mapping (AniBridge)

**Phase:** 1 · **Depends on:** T-004 · **Estimate:** L · **Risk:** high

## Goal

Make "I picked episode 5 of this anime" resolve to the right file, even when IMDb, TVDB,
TMDB and AniList disagree about seasons and numbering. Use the published
`anibridge-mappings` dataset instead of inventing a scraper.

## Scope

- Migration v2: `anime_mappings` table (flattened ranges) + `anime_mapping_overrides`.
- `services/metadata/mapping.py`:
  - download `mappings.min.json.zst` from the anibridge v3 release asset
  - decompress, validate schema version, ingest into SQLite in a transaction
  - refresh weekly (setting `anime_mapping_refresh_days = 7`)
  - lookup: `imdb_show:id:sN` ↔ `anilist:id`, `imdb_movie:id` ↔ `anilist:id`
  - episode resolution: `(imdb_show, id, season, episode) → (anilist, id, episode)`
  - reverse lookup: `(anilist, id, episode) → (imdb_show, id, season, episode)`
- `services/metadata/anime.py`: use the mapping when `kind="anime"` and the title has both
  an IMDb id and an AniList id.
- `GET /api/metadata/anime/{id}/episode-map?season=&episode=`.
- `POST /api/titles/{id}/episode-fix` to write an override.

## Out of scope

UI for bulk fixing (only the single-episode override in T-006's detail page).
AniDB-only titles with no IMDb/TVDB id.

## Acceptance criteria

- [x] Ingesting the dataset produces rows in `anime_mappings` and a coverage report
      (`imdb_show`, `anilist`, `mal`, `tvdb_show` counts) available at
      `GET /api/system/mappings`.
- [x] `(tt1234567, s1, e5)` resolves to the corresponding AniList episode when an entry exists,
      including cases where one source episode spans multiple target episodes (`|ratio`).
- [x] Contiguous ranges (`1-12`, `13-`, `1-6,8-13`) are interpreted per the schema, with tests
      for every form.
- [x] A manual override takes precedence over the dataset and survives a refresh.
- [x] If the dataset is missing or stale, anime still works in IMDb-only mode and the doctor
      reports `mapping: degraded`, not a crash.
- [x] Ingest is atomic: a corrupt download leaves the previous dataset intact.
- [x] Ingest of ~40 k entries completes in under 60 s and uses a single transaction.

## Implementation notes

Descriptor format: `provider:id[:scope]`. Providers we care about: `imdb_show`,
`imdb_movie`, `anilist`, `mal`, `tvdb_show`, `tmdb_show`. Scopes are `s{season}` for shows.

Flatten each `{source: {target: {ranges}}}` into rows:
`(src_provider, src_id, src_scope, tgt_provider, tgt_id, tgt_scope,
  src_start, src_end, tgt_start, tgt_end, ratio)`.

Mapping is **unidirectional**; build reverse rows explicitly at ingest (the dataset may
already contain them — dedupe by unique index).

Source ranges are a single contiguous range `x[-y]`. Target ranges may be comma-separated
segments with an optional trailing `|ratio`; ratio `n` = each source episode spans `n`
target episodes, `-n` = each source episode spans `1/n`.

Store the dataset version/`lastUpdate` in `settings` and expose it. Verify a checksum only
if the release publishes one; otherwise compare decompressed size and schema key.

Do not download on every request. The refresh job runs in the worker; the first run must be
triggered explicitly from settings or the first anime add.

## Test plan

- `tests/mapping/test_parse.py`: every range form, ratio, multi-segment, open-ended.
- `tests/mapping/test_lookup.py`: forward, reverse, no-match, override precedence.
- `tests/mapping/test_ingest.py`: fixture `.json.zst`, atomicity on corrupt input, count check.
- `tests/mapping/test_refresh.py`: weekly scheduling, stale fallback.

## Completion notes

- The release asset is `mappings.json.zst`, not `mappings.min.json.zst` as written in the
  scope above; the ticket was updated in the implementation to use the asset that actually
  exists. The `|0` ratio that appears on 13 published rows is normalised to `1` (the schema
  allows it but gives it no meaning; all 13 are one-to-one movie entries).
- Migration v2 adds `anime_mappings` (composite natural key, open ranges stored as end `0`)
  and `anime_mapping_overrides`. Both are declared on the ORM models as well.
- Ingest is one transaction: the old rows are deleted and the new rows, row count, schema
  version and coverage land in the same transaction. The compressed file is only replaced
  after a successful ingest, so a corrupt download leaves both the DB and the cached file
  untouched.
- Reverse rows are synthesised only when the dataset does not already contain the opposite
  descriptor pair, which avoids duplicate/conflicting lookups.
- Live check against the v3.0.3 release (2026-10-23): 263,653 rows in 8.0 s, schema `3.0.3`,
  coverage `anilist` 19,548 / `mal` 21,566 / `tvdb_show` 4,755. **The published v3 dataset
  contains no `imdb_show` source descriptors** (only `imdb_movie`). IMDb-show mapping remains
  implemented and tested with the fixture, and `resolve_title_episode` falls back to
  `tvdb_id` when an IMDb-show row is absent, which is the path real anime titles take today.
- Weekly refresh is exposed as `POST /api/system/mappings/refresh` and the staleness clock is
  `anime_mapping_refresh_days` (default 7). Wiring the due check into the background worker
  is deferred until the worker loop exists (T-009/T-011); no periodic download is started
  from the app lifespan, matching the "first run must be explicit" rule.
- New endpoint `GET /api/metadata/anime/{id}/episode-map` supports both directions: with
  `season` it maps a source season episode to AniList; without it maps an AniList episode
  back to a source scope. `POST /api/titles/{id}/episode-fix` upserts one title-scoped
  override.
