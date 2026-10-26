# T-037 — Anime season structure from the aggregate provider

**Phase:** 10 · **Depends on:** T-005, T-022, T-028, T-029, T-036 · **Estimate:** L

## Goal

Anime added through AniList comes out structurally wrong. AniList models one entry per
season/cour and exposes only an episode count, while IMDb/Cinemeta/TVDB model the **series**:
season 0 for specials, season 1, season 2, and so on.

Concrete report: *Cyberpunk: Edgerunners* (`tt12590266`). Cinemeta returns a Season 00
special (the "Let You Down" music video) and Season 01 with 10 episodes. AniList returns one
10-episode entry, so the AniList path drops the special and shows only one season. For
multi-cour shows the same design loses later seasons entirely, because each season is a
separate AniList id.

Keep the anime identity (classification, AniList artwork/overview, provider defaults) while
using the **aggregate season/episode structure** as the library truth.

## Scope

### Structure source

- When the title has an **IMDb id**, hydrate episodes from Cinemeta (all seasons, including
  `Season 00`) even when `kind="anime"`. Keep the AniList id for identity and mapping.
- When the title has a **TVDB id** but no usable Cinemeta data, use the T-022 TVDB episode
  path, including specials.
- When the title has **only an AniList id** (added from the Anime tab with no aggregate
  match), keep the existing single-entry behaviour: one library season, `episode_count`
  episodes. Do not invent seasons. Offer **related seasons** from the AniList
  `SEQUEL`/`PREQUEL` relations and let the user add them explicitly.
- `metadata_source` keeps meaning "identity source"; add a separate structure source for
  refresh so `anime + cinemeta` is valid and does not fall into the current
  "Anime titles must be resolved from AniList" validation.

### Schema (next free `user_version`, append-only)

```sql
CREATE TABLE title_seasons (
  title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
  library_season INTEGER NOT NULL,
  source TEXT NOT NULL,             -- cinemeta|tvdb|anilist
  source_id TEXT NOT NULL,          -- imdb tt..., tvdb id, anilist id
  provider_season INTEGER NOT NULL,
  anilist_id INTEGER,              -- NULL when the season has no AniList identity
  absolute_offset INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (title_id, library_season)
);
```

- One row per library season. `provider_season` is the season number in the structure
  provider; `library_season` is what Jellyfin sees (`Season 00`, `Season 01`, …).
- `anilist_id` is the AniList entry that owns that season. It differs from
  `titles.anilist_id` for split-cour shows.
- `absolute_offset` is the number of episodes before this season, used to derive
  `episodes.absolute = absolute_offset + episode`.
- Deleting a title cascades the rows. Rows are created/updated only by Add, refresh, or the
  T-029 rebuild action.

### Add and refresh

- Preview and Add must show the same structure the library will get: season 00 tab and every
  aggregate season, not a synthetic season-1 list.
- `sync_episodes()` keeps one row per `(season, episode)`. Absolute numbers become cumulative
  across seasons for anime instead of restarting at 1 in every season.
- Refresh walks every `title_seasons` row:
  1. fetch that provider season;
  2. leave an existing season untouched when its provider is unreachable and the season still
     has media or downloads;
  3. apply T-029 season offsets and episode overrides after upserting;
  4. never delete a season row that has media/files without an explicit user action.
- Rebuilding an existing title (T-029 "Fix metadata" → **Rebuild structure**) renumbers the
  existing episode rows positionally, preserving episode ids, saved releases, downloads and
  media links. It never deletes files on disk.

### Resolution

- `resolve_title_episode()` and any AniList episode lookup use the per-season `anilist_id`
  from `title_seasons` when present, falling back to `titles.anilist_id`.
- Release discovery keeps using the IMDb id when available (Torrentio already accepts
  `tt...:season:episode`); AniList-only titles keep the current `anilist:{id}` behaviour.
- `GET /api/titles/{id}` gains a `seasons` block (`library_season`, `provider_season`,
  `source`, `anilist_id`, `episode_count`) so the UI can explain the structure.

### API / UI

- `POST /api/titles/{id}/seasons/rebuild` body `{source: "auto"|"imdb"|"tvdb"|"anilist",
  confirm?: bool}`. Returns the new season list and how many episode rows were kept.
  Destructive only when it would drop episodes with files; require `confirm=true` then.
- `PUT /api/titles/{id}/seasons` replaces the per-season AniList identity set for
  AniList-only titles (used by "Add related seasons"). Reject duplicate AniList ids and
  non-contiguous library seasons.
- Fix metadata dialog gets a **Seasons** section: current rows, **Rebuild from IMDb**,
  **Add related seasons** (AniList-only), and a warning listing episodes/downloads that would
  be renumbered.
- Existing library rows are never auto-mutated. The user repairs Edgerunners once with
  **Rebuild from IMDb**; after that a refresh keeps the structure.

## Out of scope

Bulk reclassification, merging two existing library titles, creating brand-new episodes for
seasons the provider does not list, fan-edit ordering, watching-state migration, changing
Jellyfin item ids, deleting files.

## Acceptance criteria

- [x] Adding *Cyberpunk: Edgerunners* from a search result that carries `tt12590266` creates
      **one** anime title with Season 00 (the special) and Season 01 (10 episodes), matching
      the Cinemeta structure; the title keeps its AniList id and classification.
- [x] A multi-cour anime with separate AniList entries (e.g. Mushoku Tensei) gets all
      aggregate seasons under one library title, with no duplicate episode rows.
- [x] An AniList-only add with no aggregate match still creates exactly one season and offers
      related seasons; nothing is merged without confirmation.
- [x] `episodes.absolute` is cumulative across seasons (Season 02 Episode 01 follows the last
      absolute number of Season 01) and is still visible in the episode list.
- [x] Refreshing an anime with a rebuilt structure is idempotent and does not drop specials,
      seasons with files, or T-029 overrides.
- [x] The per-season AniList id is used for episode mapping; a split-cour Season 02 resolves
      to its own AniList entry, not `titles.anilist_id`.
- [x] Rebuilding Edgerunners through the fixer keeps its existing episode ids, saved releases
      and downloaded files.
- [x] No existing title changes structure until the user runs the rebuild action.
- [x] Preview shows the same season tabs and episode counts as the resulting library title.

## Test plan

- `tests/db/test_migrations.py`: `title_seasons` table, cascade, primary key.
- `tests/metadata/test_resolve.py`: anime + IMDb uses Cinemeta structure; anime + AniList only
  stays single-season; season 00 preserved.
- `tests/routers/test_add_title.py`: Edgerunners fixture creates Season 00 + Season 01;
  split-cour fixture; AniList-only add unchanged.
- `tests/services/test_refresh.py`: idempotent refresh, seasons with files kept, overrides
  applied, absolute offsets.
- `tests/routers/test_metadata_fixer.py`: rebuild keeps episode ids and releases, destructive
  confirmation, per-season AniList identity.
- `web/src/pages/__tests__/title-episodes.test.tsx`: Specials tab plus multiple seasons from
  the `seasons` payload.
- `web/src/pages/__tests__/metadata-fixer.test.tsx`: rebuild warning and result line.

## Implementation notes

- The T-028 Add path currently replaces `detail.episodes` with an AniList synthetic list
  (`titles.py` around the preview/add branches). That is the line that drops specials and
  extra seasons; remove it for titles with an aggregate id.
- Do not overload `titles.metadata_source` with structure semantics. A separate
  `structure_source` (or the `title_seasons.source` rows) keeps T-022's source switcher
  honest.
- AniBridge already maps IMDb/TVDB season/episode → AniList; use it to fill `anilist_id`
  per season when the aggregate is available. Fall back to `titles.anilist_id` only for
  Season 01 when the mapping is missing.
- Cumulative absolute numbers must be computed from the ordered season list, not from
  AniList's per-entry episode number.
- Keep the new table append-only; do not edit migration v7.
