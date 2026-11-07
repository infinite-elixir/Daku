# T-057 — Fresh downloads in Continue Watching

**Phase:** 14 · **Depends on:** T-055 · **Estimate:** S

## Goal

A title that was just downloaded should be one click away even before it has been played.
Continue Watching currently needs mpv progress, so a fresh season or movie only appears after
the user opens it from somewhere else. This ticket adds downloaded-but-unwatched files to the
same strip.

## Decisions

- Playback cards keep priority. One card per **title**; a title with any present playback
  row is never also added as a fresh download.
- A fresh card uses the earliest downloaded **unwatched** episode (`season, episode` order,
  Season 0 ignored). If every downloaded episode is marked watched, the title drops off.
- A movie without playback uses its primary file. Extras and non-primary episode-less files
  are ignored.
- A finished movie, or a finished episode with no unwatched follow-up, stays out of the
  strip and is not re-added as "new".
- `Up next` cards now skip episodes whose `episodes.watched` flag is set.
- Fresh cards are ordered by the newest `media_files.created_at` for that title, merged with
  played cards by their timestamps and capped at 12.
- The API adds `is_new` and allows `played_at` to be `null`; the UI shows a `New` badge.
- Read-only: no download, episode or watch-status state is written.

## Scope

- `repositories/continue_watching.py`: fresh-download selection and merge.
- `routers/continue_watching.py`: `is_new`, nullable `played_at`.
- `ContinueWatching.tsx`, `api.ts`: `New` badge.
- Backend and frontend tests. No migration.

## Acceptance criteria

- [x] A newly downloaded, never-played movie appears with `is_new: true` and no seek bar.
- [x] A newly downloaded series shows its earliest unwatched episode, skipping watched ones.
- [x] A title with progress does not get a second fresh card.
- [x] Finished movies and fully watched series are absent.
- [x] `Up next` skips manually watched episodes.
- [x] Results stay capped at 12 and remain newest first.
- [x] `scripts/check.sh --web` passes.
