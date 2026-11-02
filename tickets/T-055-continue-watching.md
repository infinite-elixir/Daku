# T-055 — Continue watching

**Phase:** 14 · **Depends on:** T-054 · **Estimate:** M

## Goal

Put a horizontal Continue Watching strip above the library poster grid, so the last file
watched is one click away without opening its title page. Cards copy the title page's
downloaded-files strip (thumbnail, episode label, filename, size, seek bar, hover play
badge) and carry the title name.

Continue Watching is a shortcut to the existing play action, not a new player feature.
Daku still never decodes, seeks or resumes: launching a card starts the host mpv at 0:00.

## Decisions

- Lives on `/library`, between the filter chips and the poster grid, independent of the
  active filters and of order of operations.
- One card per **title**, pointing at a single media file. Newest `playback_updated_at`
  first, capped at 12.
- Source of truth is mpv playback progress only (`media_files.playback_*`). Manually
  ticking an episode watched never creates a row.
- A file with `playback_position_s > 0` and a known duration under 95% is the card.
- If the most recently played file is ≥95% finished and it is an episode, the card becomes
  the next downloaded episode (ordered by `(season, episode)`, Season 0 ignored) and is
  marked **Up next**. With no next downloaded episode the title drops off entirely.
- An in-progress file always wins over an "Up next" file for the same title.
- Files whose duration is unknown are skipped. Season 0 specials are ignored entirely.
- Titles with `watch_status` `dropped` or `on_hold` never appear.
- Rows whose file is no longer present on disk are skipped.
- Clicking a card launches the host player exactly like a downloaded-files card (or opens
  the file's title files page when playback is disabled).
- The section renders nothing when empty.

## Out of scope

- Resume/seek from the stored position, per-file rows, a "recently finished" row,
  a see-all page, watch-status writes from this section.

## Scope

- `GET /api/continue-watching` returning up to 12 items with the title fields and the
  existing media-file payload.
- `repositories/continue_watching.py`: choose the per-title file (in progress, else next
  downloaded episode), drop missing files, honour watch status.
- `ContinueWatching.tsx`, a four-across strip variant, an optional title line and `Up next`
  badge on `MediaFileCard`.
- No migration.

## Acceptance criteria

- [x] `GET /api/continue-watching` returns at most 12 items, newest first, one per title.
- [x] A movie or episode at 40% appears with its stored seek bar; a 100% file without a
      downloaded follow-up does not.
- [x] A finished episode with a downloaded next episode returns that next file with
      `up_next: true`; an in-progress episode wins over it.
- [x] Files with no duration, Season 0 files, missing files, and `dropped`/`on_hold`
      titles are excluded.
- [x] The Library page renders the strip above the poster grid, hides it when empty, and
      clicking a card uses the same launch path as the downloaded strip.
- [x] Backend tests and `pnpm -C web test` cover the rules above.
- [x] `scripts/check.sh` passes.

## Implementation notes

- The endpoint reuses the media-file serializer moved to `routers/payloads.py` so the
  title files endpoint and Continue Watching cannot drift.
- `ContinueWatching` reuses `useLivePlayback` so the currently playing card uses the live
  position over the persisted one.
- Cards pass `bleed` to `MediaFileCard`: the artwork touches the card's outer border with
  no inner rounded box, matching `LibraryCard`'s poster treatment. The title page's
  downloaded strip keeps its padded inner art.
