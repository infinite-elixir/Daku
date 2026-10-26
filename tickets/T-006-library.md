# T-006 — Library: CRUD API and mobile UI

**Phase:** 1 · **Depends on:** T-003 · **Estimate:** L

## Goal

Search, add, browse and remove titles. This is the first genuinely usable screen.

## Scope

- `routers/titles.py`:
  - `POST /api/titles` `{source, source_id, kind}` → fetch metadata, insert, fetch episodes.
    **Never** creates a download.
  - `GET /api/titles?kind=&availability=&wanted=&q=&sort=`
  - `GET /api/titles/{id}` (detail + episodes + saved release count)
  - `PATCH /api/titles/{id}` (`wanted`, `folder_name`, `kind`)
  - `DELETE /api/titles/{id}?keep_files=true`
- `services/metadata/resolve.py`: one function that takes a candidate and returns a fully
  hydrated `TitleDetail`, whichever source it came from.
- UI:
  - `/search` — debounced input, kind tabs (All / Movies / Series / Anime), result grid,
    "Add" button with optimistic state.
  - `/library` — poster grid, availability chip (`Saved / Partial / Available`), kind filter,
    "Wanted" and "No file yet" filters, empty state.
  - `/title/:id` — hero, metadata, season/episode list with per-episode status.
- Bottom tab bar shell shared by all routes.

## Out of scope

Releases (T-007), downloads (T-009), artwork caching to disk, editing metadata.

## Acceptance criteria

- [x] Adding a movie persists it and it survives a process restart.
- [x] Adding a title does **not** create a `downloads` row, contact qBittorrent/aria2, or
      write to disk. Adding with no network available still stores the entry.
- [x] A title with no files returns `availability="none"` and the UI renders it as
      **"Saved"**, never as an error or "missing".
- [x] `PATCH {wanted: true}` marks intent and is filterable via `?wanted=true`;
      it does not start a download.
- [x] Adding a series persists all episodes with `season`, `episode`, `absolute=null`,
      `status="missing"`.
- [x] Adding an anime persists the AniList entry and its episodes without duplicating
      already stored `(title_id, season, episode)` rows.
- [x] Re-adding the same title is idempotent and returns the existing row with `200`,
      not a duplicate or a `500`.
- [x] Library grid shows poster, title, year, kind and availability; it is usable at 390 px
      and at 1440 px with the same component tree.
- [x] Search is debounced at 300 ms, cancels in-flight requests, and shows a skeleton.
- [x] `DELETE` cascades DB rows but leaves files on disk by default.
- [x] Every list response is paginated (`limit`, `offset`) and stable-sorted.

## Implementation notes

Uniqueness: one title per `(imdb_id)` or `(anilist_id)`. Add a partial unique index on each
non-null column and catch the conflict to return the existing row.

Status derivation: `none` (no available episode), `partial` (some), `complete` (all non-special
episodes available). Recompute in the repository, not in the router.

`POST /api/titles` is the only place that writes titles from metadata; it always resolves
through `resolve.py` so IMDb, Cinemeta, AniList and Jikan behave identically.

UI state: TanStack Query with `staleTime: 30_000`. Poster URLs are remote; use
`loading="lazy"` and a solid placeholder, no layout shift.

Mobile layout: 2-column poster grid at < 480 px, 4 at < 900 px, 6 above. Tap target ≥ 44 px.
Kind tabs must not wrap into two rows.

## Test plan

- `tests/routers/test_library.py`: add movie/series/anime, idempotent re-add, list filters,
  pagination, patch, delete with `keep_files=true`, cascade counts.
- `web/src/pages/__tests__/library.test.tsx`: renders grid, empty state, filter.
- Manual: 390 px viewport, add three titles, restart server, confirm persistence.

## Implementation notes (completed)

- Migration **v3** adds `titles.folder_name` plus partial unique indexes on `imdb_id` and
  `anilist_id`. T-014's Jellyfin ids therefore move to migration v4.
- `services/metadata/resolve.py` normalises IMDb/Cinemeta and AniList into one `TitleDetail`.
  AniList does not list episodes, so season 1 is derived from `episode_count` and `absolute`
  is stored for display.
- `POST /api/titles` accepts optional `title`, `year` and `poster_url` fallbacks so an add
  still succeeds when metadata is unreachable. It returns `201` for a new row and `200` for
  an existing one.
- Anime persistence uses `services/metadata/models.EpisodeRef` through
  `repositories/episodes.sync_episodes`, which skips existing `(title_id, season, episode)`.
- `PATCH` supports `wanted`, `folder_name` and `kind`; `wanted_at` follows the intent flag.
- `DELETE` removes recorded `media_files.src_path`/`lib_path` only when `keep_files=false`.
- Availability (`none` / `partial` / `complete`) is derived in `repositories/titles.py` from
  `media_files`, so it cannot be set by hand.
- Deferred: the single-episode mapping override UI mentioned by T-005 is not part of this
  ticket's scope; the backend endpoint already exists.

