# T-022 — Metadata re-hydration and source switcher

**Phase:** 6 · **Depends on:** T-003, T-004, T-021 · **Estimate:** L

## Goal

Fix library entries that were added while metadata was unreachable and therefore have **zero
episodes** (today: `Cyberpunk: Edgerunners`, `tt12590266`). Add an explicit, repeatable way to
re-fetch metadata and to choose the source for a single title.

Source policy: **IMDb is authoritative for identity**, Cinemeta is the IMDb-backed detail
source, then TheTVDB, then AniList. AniList remains the anime mapping source.

## Root cause (verified 2026-10-23)

`POST /api/titles` stores `services.metadata.resolve.fallback_detail` when metadata is
unreachable. That row has `overview=null` and no episodes, and because re-adding is
idempotent, nothing ever backfills it. The local DB confirms `tt12590266` has 0 episodes
while Cinemeta currently returns 10 season-1 episodes plus specials for that id.

## Scope

### Schema (migration v5, append-only)

- `titles.metadata_source` — `NULL` (auto) or one of `cinemeta`, `tvdb`, `anilist`.
- `titles.metadata_synced_at` — UTC ISO-8601 string or `NULL`.
- No destructive changes. `imdb_id`, `anilist_id` and `tvdb_id` keep their current meaning;
  a refresh may fill `tvdb_id`/`tmdb_id` when a source returns them but **never rewrites
  `imdb_id`**.

### Source resolution

Priority per title:

1. explicit `titles.metadata_source`, if set;
2. otherwise `settings.metadata_sources`, default `["cinemeta", "tvdb", "anilist"]`;
3. skip a source that cannot answer (no key, no IMDb id, not anime) and continue.

Available sources:

| Id | Label | Covers | Notes |
|---|---|---|---|
| `cinemeta` | IMDb / Cinemeta | movie, series | Episodes + rating + rich metadata. This is the IMDb-backed path. |
| `tvdb` | TheTVDB | series, anime | Requires `tvdb_api_key`; IMDb id → TVDB id via `GET /search/remoteid/{imdb_id}`. |
| `anilist` | AniList | anime | Existing adapter; episodes derived from `episode_count` when AniList lists none. |

`imdb` is deliberately **not** a separate adapter: IMDb has no public episode-list API and
Daku does not scrape it. The dropdown labels the first entry **IMDb / Cinemeta** so the
priority is visible without inventing a fragile integration.

### TheTVDB adapter (`services/metadata/tvdb.py`)

- Auth: `POST https://api4.thetvdb.com/v4/login` with `{"apikey": <key>}` → bearer token,
  valid ~1 month. Cache the token in memory and persist it with the existing metadata cache;
  re-login on `401` once, never log the key or the token.
- Resolve: `GET /search/remoteid/{imdb_id}` → first result with `type == "series"` → id.
- Episodes: `GET /series/{id}/episodes/official?page=N`, following `links.next` until
  exhausted. Map `seasonNumber`, `number`, `name`, `aired`, `absoluteNumber`.
- Cache responses for 24 h. On `429` honour `Retry-After` once, then degrade to the next
  source; never fail an Add or a detail read because TVDB is down.
- Config: `tvdb_api_key` in `secrets.json`, with `DAKU_TVDB_API_KEY` support. Empty key means
  the source is listed but unavailable (`NO_KEY`).
- `/api/system/doctor` reports `tvdb: configured|unconfigured` (no network call).

### API

- `GET /api/titles/{id}/metadata/sources` →
  `{sources: [{id, label, available, reason}], selected: <id|null>, synced_at}`
  where `reason` is `NO_KEY`, `NO_IMDB_ID`, `NOT_ANIME`, `UNSUPPORTED_KIND` or `null`.
- `POST /api/titles/{id}/metadata/refresh` body
  `{source?: "auto"|"cinemeta"|"tvdb"|"anilist", force?: bool}`:
  - resolves through the priority above, or the requested source;
  - upserts episodes by `(season, episode)` via `repositories/episodes.sync_episodes`;
  - updates title, year, overview and poster only when the fetched value is non-empty;
  - fills `tvdb_id`/`tmdb_id` when present and empty;
  - sets `metadata_source` when an explicit source was requested, and `metadata_synced_at`;
  - **never deletes** an episode row, and never clears `media_files`, `status`, or watched
    state (T-025) on an existing row;
  - returns `{title, report}` with `source`, `added`, `updated`, `skipped`, `stale`;
  - emits a `metadata.refreshed` event.
- `GET /api/titles/{id}` gains `metadata_state`: `"empty"` when the title is not a movie and
  has zero episodes, `"stale"` when `metadata_synced_at` is older than 30 days, else `"ok"`.
  This keeps the read endpoint side-effect free.
- `AddTitleRequest` accepts `metadata_source` (from the T-021 preview). Re-adding an existing
  title with a different source does **not** overwrite it silently; it returns the row and the
  UI can call refresh.

### UI

- Shared metadata bar on the preview and title page: a **source dropdown** with
  IMDb / Cinemeta, TheTVDB and AniList. Unavailable entries stay visible but disabled with the
  reason as a tooltip. Choosing a source on a library title calls refresh and persists the
  choice; choosing one on a preview only re-fetches the preview.
- **Refresh metadata** button with pending state and a concise result line
  ("10 episodes added"). Errors are inline, not fatal.
- Auto-repair: when `metadata_state == "empty"`, the UI calls refresh **once per mount** for
  that title (guarded by `sessionStorage` so a failing source cannot loop). While it runs,
  show "Fetching episodes…" instead of "No episodes".
- After a successful refresh, invalidate the title query and the library query.

## Out of scope

Ratings and extra metadata (T-024). Changing Torrentio ids or episode numbering. Jikan,
TMDB and IMDb scraping adapters. Watched state.

## Acceptance criteria

- [ ] Migration v5 adds `titles.metadata_source` and `titles.metadata_synced_at` without
      touching existing rows.
- [ ] Refreshing `tt12590266` (Cyberpunk: Edgerunners) adds its 10 season-1 episodes; season-0
      specials are stored but remain specials.
- [ ] Refreshing twice adds no duplicates and preserves existing episode ids.
- [ ] A title added while offline recovers its episodes without deleting and re-adding it.
- [ ] Refreshing never deletes episode rows, clears `media_files`, or changes watched state.
- [ ] The source endpoint lists Cinemeta, TVDB and AniList with accurate availability; TVDB
      is unavailable without a key, AniList is unavailable for non-anime.
- [ ] Selecting a source persists on the title and is used by the next refresh.
- [ ] `metadata_state == "empty"` triggers at most one automatic refresh per page load and
      shows a retry on failure, never a loop.
- [ ] TVDB pagination is followed; a `429` or malformed payload degrades to the next source
      and the refresh still returns a useful report.
- [ ] The bot token, TVDB key and TVDB token never appear in logs or API responses.
- [ ] `/api/system/doctor` reports TVDB as configured or unconfigured.
- [ ] Tests use recorded fixtures and `respx`; no live calls in CI.

## Test plan

- `tests/metadata/test_tvdb.py`: login, remote-id lookup, two pages of episodes, 401 re-login,
  429 fallback, missing key, malformed payload.
- `tests/routers/test_metadata_refresh.py`: Cyberpunk regression, idempotent second refresh,
  explicit source, auto priority, preserved `media_files`, offline-added row, preview source
  error paths.
- `tests/db/test_migrations.py`: v4 → v5 upgrade.
- `web/src/pages/__tests__/metadata-switcher.test.tsx`: disabled reasons, refresh result,
  one-shot auto repair.

## Implementation notes

- Keep the existing `resolve_candidate` contract for Add. Refresh is a separate, explicit
  operation so `GET` stays safe and `POST /api/titles` stays idempotent.
- Reuse `sync_episodes`; extend it only if it needs an "update names/dates, keep status" mode.
- The TVDB token cache must not be written to `secrets.json` or `config.json`.
- Update `AGENTS.md` §6 with the verified TVDB contract and §4 with `tvdb_api_key` when this
  lands, not before.
