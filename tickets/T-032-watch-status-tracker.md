# T-032 — Local watch-status tracker

**Phase:** 9 · **Depends on:** T-006, T-021 · **Estimate:** L

## Goal

Track what you plan to watch, what you are watching and what you finished, locally. This
replaces the boolean `watched` idea from T-025 with an AniList-style status per title plus
per-episode progress. No external account, no sync, no scrobbling.

This ticket **supersedes the watched schema/API/UI in T-025**; T-025 keeps only its toast work.

## Scope

### Schema (next free `user_version`, append-only)

```sql
ALTER TABLE titles ADD COLUMN watch_status TEXT;          -- NULL = untracked
ALTER TABLE titles ADD COLUMN status_changed_at TEXT;
ALTER TABLE episodes ADD COLUMN watched INTEGER NOT NULL DEFAULT 0;
ALTER TABLE episodes ADD COLUMN watched_at TEXT;
```

- Allowed `watch_status`: `planning`, `watching`, `completed`, `dropped`, `on_hold`, or `NULL`.
- Existing rows migrate as `NULL` (untracked). No backfill guesses.
- Availability stays derived from `media_files`; status never changes it.

### Semantics

- **Movies**: `watch_status` is the source of truth. `completed` means watched; `watching` is
  allowed but has no extra meaning beyond "started"; `dropped`/`on_hold`/`planning` are intent.
- **Series/anime**: `episodes.watched` is the progress source of truth. `watch_status` is the
  user's intent and is never overwritten by episode toggles *except* for the two automatic
  transitions below.
- Automatic transitions, applied in the same transaction as an **episode write** (never on an
  explicit title status change, so a manual choice always wins):
  1. if at least one non-special episode is watched and status is `NULL` or `planning` →
     `watching`;
  2. if every non-special episode is watched and at least one exists and status is `watching`
     or `planning` → `completed`.
- `dropped` and `on_hold` are **sticky**: episode changes never move them. The user must
  explicitly change the status.
- Setting `completed` on a series marks every non-special episode watched and sets
  `watched_at`. Moving from `completed` to any other status clears episode flags only when the
  user confirms (`clear_progress=true`); otherwise progress is kept.
- Season 0 specials never contribute to completion and are never bulk-marked.
- `wanted` is unchanged and orthogonal: `wanted` = still to acquire; `watch_status` = where you
  are in watching. A title can be `wanted=true` and `completed=true` (seen elsewhere, still
  collecting a better copy).
- Deleting a title cascades episodes and their progress.

### API

- `PATCH /api/titles/{id}` accepts `watch_status: "planning"|"watching"|"completed"|"dropped"|
  "on_hold"|null`, plus `clear_progress?: bool` for leaving `completed`.
- `PATCH /api/titles/{id}/episodes/{episode_id}` accepts `{watched: bool}`; returns
  `404 EPISODE_NOT_FOUND` when the episode belongs to another title.
- Title payloads gain:
  `watch_status`, `status_changed_at`, `watched_episode_count`,
  `watchable_episode_count`, `next_episode` (`{season, episode, name}` or `null`).
- `GET /api/titles` filters:
  - `status=planning|watching|completed|dropped|on_hold|untracked`
    (repeatable/comma-separated for multi-select);
  - `watched=true|false` kept as a derived convenience (`completed` vs not), documented as such;
  - sorting by `status_changed_at` for "recently watched".
- Every status or episode change emits `watch.updated`; the worker is not involved.

### UI

- Title header: a status control next to **Wanted** — a compact select or segmented menu with
  the five states plus **Untracked**. On mobile it is a bottom sheet; no more than one primary
  action.
- Series/anime: progress line `4 / 12 watched` with a thin progress bar (neutral or success,
  not volt; volt is reserved for downloads). **Next up: S01E05** appears when status is
  `watching`.
- Episode rows: a watched checkbox at the far right. It stops propagation and does not change
  the selected episode. Bulk actions **Mark season watched** / **Mark previous watched** are
  allowed only from the season header menu.
- Library: filter chips **Planning / Watching / Completed / Dropped / On hold / Untracked**
  (multi-select) in addition to Wanted and No file. Cards show a small status label and a
  progress ring/bar; do not tint the whole card.
- Search results and preview never show status controls; a preview may show `In library:
  Watching` when the title already exists.
- Status changes are optimistic, revert on error, and announce via toast when T-025 has landed.
- Empty state copy for the new filters must explain them, not say "no results".

## Out of scope

AniList/MAL/Trakt sync, scrobbling, Jellyfin watched sync, playback detection, ratings,
favourites, per-user state, watch history beyond `watched_at`/`status_changed_at`, "time
watched" statistics, and automatic status changes based on downloads.

## Acceptance criteria

- [x] Migration adds the three columns safely; existing titles read as untracked and unwatched.
- [x] A movie can be set to each status and the change survives a restart.
- [x] Marking one episode of a planning series moves it to watching; marking the last
      non-special episode moves it to completed.
- [x] Marking a series completed sets every non-special episode watched; specials stay
      untouched.
- [x] Moving away from completed keeps progress unless `clear_progress=true`, and the UI asks
      first.
- [x] `dropped` and `on_hold` are not changed by episode toggles.
- [x] An explicit status change is never immediately overwritten by an automatic transition.
- [x] `next_episode` is the first unwatched non-special episode in season/episode order.
- [x] Library status filters compose with kind, wanted and availability filters.
- [x] `watched=true` returns completed titles consistently; it does not change `wanted`.
- [x] The episode checkbox does not steal the row's release-search click.
- [x] Preview has no status controls.
- [x] The control is keyboard operable and readable at 390 px.

## Test plan

- `tests/db/test_migrations.py`: upgrade, defaults, cascade.
- `tests/routers/test_watch_status.py`: every status, movie, series auto transitions,
  sticky dropped/on_hold, completed bulk, clear_progress, specials excluded, cross-title 404,
  filters, events.
- `tests/repositories/test_episodes.py`: next_episode ordering, progress counts.
- `web/src/pages/__tests__/watch-status.test.tsx`: header control, progress, episode toggle,
  season bulk, library filters, preview absence.
- Regression: T-021 deep links and T-025 toasts still pass.

## Implementation notes

- Derive `watched_episode_count`, `watchable_episode_count` and `next_episode` in the repository;
  do not store duplicates.
- Recompute automatic transitions inside the episode transaction, not in the router.
- Do not use `watch_status` to gate downloads, availability or Jellyfin scans.
- `completed` on a series with zero episodes is allowed (it means "seen elsewhere"); it sets no
  episodes and does not fail.
- When T-029 changes a title's kind to movie, clear episode progress only with confirmation;
  the fixer ticket owns that warning.
- Update `AGENTS.md` §5 (episodes/titles), §7 (status is not a download state) and plan.md
  decision #13.
- The header control is a native `<select>` at every breakpoint. On mobile the OS picker
  stands in for a custom bottom sheet; it stays keyboard operable and needs no extra state.
