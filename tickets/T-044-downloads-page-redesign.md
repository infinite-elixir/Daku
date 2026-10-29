# T-044 — Downloads page: three sections, grouped cards

**Phase:** 12 · **Depends on:** T-039, T-042, T-043 · **Estimate:** M

## Problem

`DownloadsPage` renders one flat list of every download for every title, active and finished
interleaved. A single `DownloadRow` then stacks seven facts into two lines:

```
Cyberpunk Edgerunners
[Complete] S01E03 [S01E03 · 244.7 MB] Files 1080p Torrent
```

- the title is 15px, smaller than the status pill it shares a row with;
- the episode appears twice, from `episode_label` and again from `selection_label`;
- "Files" is an underlined anchor pointing at `#release-files-<release_id>`, an element that
  only exists inside `MagnetFiles` on the title page. On Downloads it scrolls nowhere;
- while a pack is active the card still lists `Downloaded S01E01 S01E02 S01E03` as pills, so
  the same card claims files are downloaded and downloading at once;
- there is no total space used, and no signal that `244.7 MB` is *what this download fetches*
  rather than what is on disk.

## Goal

Answer two different questions in two different places: **what is happening** and **what I
have**. One card hierarchy, one meaning per number.

## Scope

### Sections

Derived on the client from `status`; no API change and no new state.

| Section | Statuses | Rendering |
|---|---|---|
| **Needs attention** | `failed`, `cancelled` | Rendered first, only when non-empty |
| **Downloading** | `queued`, `active`, `paused`, `organizing` | Always rendered (empty state) |
| **Completed** | `complete`, `available` | Always rendered (empty state) |

- Section heading: `text-h2` (22px), count in `--text-muted`.
- Inside each section, group by `title_id`; a title with several downloads gets one group
  header and several cards.
- Group header: title name (`text-title`, links to `/title/{id}`), then a muted
  `Files (N)` link to `/title/{id}/files` when `downloaded_files` is non-empty, then the
  aggregate size for that group.
- Never nest links: header title and Files link are siblings.

### Card hierarchy

`DownloadRow` is rewritten. Four lines, top to bottom, one job each:

1. **Title** — `text-title` 17px, one line, truncate with a `title` attribute. Status pill
   moves to its own aligned slot on the right, vertically centred with the title.
2. **Meta** — one line, `text-small` `--text-secondary`: `S01E03 · 1080p · Torrent`. At most
   three facts. Quality and engine are dropped individually when unknown.
3. **Progress** — unchanged bar, full width.
4. **Numbers** — two sides. Left: `Downloading 244.7 MB of 1.2 GB · 41%`. Right, only while
   active: speed and ETA.
5. **Library line** — `In library 2.1 GB · 3 files`, muted, with the file labels as plain
   text, truncated at three names plus `+N`: `In library: S01E01, S01E02, S01E03 +2`.

Rules:

- **Delete the "Files" link.** A download card is a status object; the only file list lives
  on the title page (`MagnetFiles`) and the files page (T-047).
- **Delete the selection pill.** Its content becomes the `Downloading …` figure, labelled
  with the episode when there is one: `Fetching S01E03 · 244.7 MB`.
- **Delete the duplicate episode label.** `episode_label` appears exactly once, in the meta
  line.
- **Two sizes, never adjacent.** "Fetching" is what this download will pull; "In library" is
  what already sits on disk. When they are the same single file, collapse to one line and no
  "In library" figure.
- **Accent is only for what is fetching now.** The library line never uses `--accent`.
- Keep the existing `FILE_SELECTION_FAILED` rewrite and the error block.
- Keep actions: Pause/Resume, Cancel, Retry, Remove (with the existing dialog and
  `delete_files` behaviour). Primary action is the first button in the row, right-aligned on
  wide and full-width on narrow.

### Empty and error states

- Downloading empty: "Nothing is downloading. Queue a release from a title page."
- Completed empty: "No completed downloads yet."
- Needs attention hidden entirely.
- Keep the existing page-level error + Retry.
- Keep SSE-driven optimistic updates and invalidation exactly as they are; this ticket is
  presentation only.

## Out of scope

Grouping by anything but title, a downloads filter/search UI, a new status, re-inspecting
files, the title page (T-045), file cards (T-047).

## Acceptance criteria

- [ ] The page shows Needs attention / Downloading / Completed, in that order; the first is
      hidden when empty.
- [ ] Downloads are grouped by title inside each section; the group header shows the title,
      an aggregate size and a `Files (N)` link.
- [ ] A card shows title, one meta line, progress, numbers, and at most one library line.
- [ ] The episode label renders once; no selection pill remains.
- [ ] "Files" no longer appears on a download card and no dead anchor exists on the page.
- [ ] Sizes are labelled: what is fetching and what is in the library are never side by side
      as unlabelled numbers.
- [ ] During an active pack download, already-downloaded episodes appear only in the muted
      `In library:` line, never as accent pills.
- [ ] Failed and cancelled rows appear in Needs attention with Retry/Cancel/Remove working.
- [ ] At 390px no card scrolls horizontally and the action row wraps without clipping.

## Test plan

- `web/src/pages/__tests__/downloads.test.tsx`:
  `sections_split_active_completed_and_failed`,
  `groups_downloads_by_title`,
  `does_not_render_the_dead_files_link`,
  `renders_the_episode_label_once`,
  `labels_fetching_size_and_library_size`,
  `hides_already_downloaded_pills_while_active`.
- `web/src/components/__tests__/download-row.test.tsx`: hierarchy classes, collapsed single
  file case, `+N` truncation, error copy.
- Manual: a 3-episode pack mid-download, a finished movie, a failed row, a cancelled row.

## Implementation notes

- Group with a `Map<title_id, DownloadRecord[]>` per section; sort groups by `updated_at`
  descending, cards inside a group by `created_at`.
- `downloaded_files` is already in the payload (T-042); sum `size_bytes` for the library
  figure and treat a null size as 0 rather than "unknown".
- Do not add `lib_path` or any host path to the payload for this ticket.
- The section headings are `h2`; the group title is `h3`; the card title is `h4`.
