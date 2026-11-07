# T-056 — Release options: collapsible files, pack flow, dead releases

**Phase:** 12 · **Depends on:** T-030, T-031, T-046 · **Estimate:** M

## Problem

T-046 introduces the release options menu (level 3) but deliberately stops at the shell: release
identity, the episode-aware Download, save and copy. The file list still lives in the old
inline `MagnetFiles` block, dead saved releases still need a route back to Find, and the
Saved-tab rows have not yet been proven to use the same detail screen.

Until this lands, level 3 cannot answer "what is in this torrent?" or "this release is dead —
what now?", which are the two reasons the options menu exists.

## Goal

Level 3 is the complete per-release screen. Files are a collapsible section of it, the file
selection feeds the existing episode download flow, and a dead release offers `Find another`
without the user leaving the episode.

## Scope

### Files section

- `MagnetFiles` renders inside a collapsible `Files` section on level 3; its trigger is the one
  source of truth and the release row never renders a second Files button.
- Opens automatically when level 3 opens (`defaultOpen`) and stays collapsible. The trigger
  shows `Files (N)` when the probe has already returned a count, otherwise `Files`.
- Expanded: the existing file list (name, size, per-file state) inside the options pane; the
  section inherits the pane's scroll container, never creates a second scrollbar.
- Action row at the bottom of the expanded section: `Download selected` on the left, the
  selection count on the right. Disabled until a file is selected, with the existing reason
  text.
- Selecting a file keeps the current priority/selection contract (T-031/T-041); this ticket
  only moves the UI, it does not change engine behaviour.
- Probe errors keep the existing codes and copy; a probe timeout leaves the section open with
  Retry, not a stuck spinner.

### Download flow

- `Download S01E03` remains the only primary on level 3; the confirm → chooser flow from
  `EpisodeDownload` is unchanged.
- The chooser and the inline Files section agree on the selected file: opening the chooser
  preselects the file chosen in Files, and choosing a file in the chooser scrolls/highlights it
  in the list.
- "Download whole pack" stays inside the chooser behind its checkbox. It is not duplicated on
  the options screen.
- A queued download keeps the single-download-per-release behaviour (T-042): re-requesting the
  same episode does not create a second queue row.

### Dead saved releases

- A dead saved release shows the existing `Dead` pill on its row and on level 3.
- Level 3 offers `Find another`:
  - from the Saved tab, switch to Find for the same episode and reset the quality/providers
    filters only if they would hide results (confirm before resetting);
  - preserve the pane and the episode selection; no page-level scroll jump.
- `Find another` never deletes the saved row.

### Saved rows

- Saved-tab rows and Find-tab rows use the same `ReleaseRow` and open the same level 3.
- Row status line keeps `Checked <time>`, `Dead`, `Manual` and the episode label where they
  exist.
- Saving and unsaving from level 3 is optimistic, `aria-pressed`, and updates the Saved count
  and list without a full refetch flash.

## Out of scope

Changing the release APIs or query keys; changing probe limits or magnet construction; the
downloads page (T-044); playback (T-048); a full file-management surface; download-whole-pack
outside the chooser.

## Acceptance criteria

- [x] Level 3 has a `Files` section that opens automatically and can be collapsed, without
      introducing a second scroll container.
- [ ] `Download selected` sits bottom-left with the selection count on the right, and is
      disabled until a file is selected.
- [ ] Choosing a file in the Files list and in the download chooser stay in sync.
- [ ] `Download S01E03` is the only accent-styled control on level 3; "Download whole pack"
      remains inside the chooser behind its checkbox.
- [ ] Re-requesting an episode that is already in the queue reuses the row, as T-042 requires.
- [ ] A dead saved release shows `Find another` on level 3; it switches to Find for the same
      episode without losing the episode selection.
- [ ] Saved and Find rows share the same row component and the same level 3.
- [ ] `Files (N)` reflects a known count, and probe failures keep the existing error copy with
      a Retry.
- [ ] No magnet string is rendered anywhere on level 3.

## Test plan

- `web/src/components/__tests__/magnet-files.test.tsx`: collapsed/expanded, selection count,
  `Download selected` disabled state, no nested scroll, probe error + retry.
- `web/src/components/__tests__/release-options.test.tsx`: file/chooser sync, single primary,
  pack stays in chooser, save/unsave optimistic.
- `web/src/pages/__tests__/releases.test.tsx`: dead saved row opens level 3, `Find another`
  returns to Find with the episode preserved.
- `tests/routers/test_downloads.py`: unchanged; add a regression assertion that a repeated
  episode request does not create a second row if the existing coverage misses it.
- Manual: season pack with 12 files, a file list longer than the pane, a dead release, probe
  timeout, 390 / 900 / 1440px.

## Implementation notes

- `MagnetFiles` already owns the trigger and the "Download selected" action; keep that
  ownership and give it a `collapsible` presentation rather than copying its markup into the
  options screen.
- Level 3 opens Files with `defaultOpen`, so opening a torrent probes its metadata once; the
  result is cached for 24 h and the section stays collapsible.
- Preserve the T-046 history contract: `Find another` changes the tab inside level 3, it does
  not push a new level.
- Keep `downloads.selected_files_json` / `file_match_json` semantics untouched; this is a
  presentation and wiring ticket.
- Skip the `Files (N)` count when the file list has never been probed; do not show `Files (0)`
  as if the torrent were empty.

## Implemented (partial)

Level 3 now renders `MagnetFiles` with `defaultOpen`, so clicking a torrent inspects it and
shows the file list immediately, with the copy control owned by the options action row. The
remaining acceptance criteria (chooser/highlight sync, dead-release “Find another”, pack-flow
polish) are still open.

File selection no longer requires the release to be saved first. `MagnetFiles` takes an
`ensureRelease` callback from `ReleasesSection` and creates the release row on the first
`Download selected`, the same way the episode download already did. Previously a Find result
with `release_id = null` rendered every checkbox but kept the button disabled, so ticking
files appeared to do nothing until the star was used. Covered by
`web/src/components/__tests__/file-selection.test.tsx`.
