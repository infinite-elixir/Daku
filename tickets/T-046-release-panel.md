# T-046 — Release panel: taller panel, one action row

**Phase:** 12 · **Depends on:** T-030, T-031, T-043, T-045 · **Estimate:** M

## Problem

Expanding an episode renders `ReleasesSection` in `mode="find"` inside the episode card, with
`max-h-[22rem] overflow-y-auto`. In practice that is one and a half releases tall, so the
panel is scrolled before the user has read a filename.

Inside a release row the controls are scattered across two clusters:

- `CopyMagnet` sits inline with the filename and prints `magnet:?xt=urn:btih:1234…` next to
  the button, which is noise nobody reads and a truncated string nobody can verify;
- the star, `Download` and the `Files` button live in a different cluster, at the end of the
  first line;
- for series, `EpisodeDownload` adds a *fourth* button (`Download S01E03`) below the row,
  next to a fifth (`Download`) at the top. Nothing explains the difference between them.

## Goal

A panel tall enough to compare releases, a filename that owns its line, and exactly one row
of actions whose primary is obvious.

## Scope

### Panel

- `mode="find"` list: full width, `max-h-[34rem]`, `overflow-y-auto`.
- The list header (`Showing N of M`, quality counts, degraded/stale badges) becomes sticky
  at the top of the scroll container with a `--bg-panel` background and a bottom border.
- Filters stay above the scroll container, not inside it.
- The empty, loading and error states keep their current copy; only the container changes.

### Release row

Three lines, fixed order:

1. **Filename** — full width, one line, `text-body`, truncate with a `title` attribute.
   Nothing else on this line.
2. **Meta** — `quality · size · seeders · SOURCE`. Source keeps its existing small badge
   (`TORRENTIO` / `NYAA`). `hintsLine` output stays a fourth muted line when present.
3. **Actions** — one row, right-aligned on wide screens, wrapping on narrow:
   `[Download] [★] [Files] [copy]`.

Rules:

- **Download is the only primary** (`bg-accent/15 border-accent/50 text-accent`). The star
  and the copy control are square 36px icon buttons. Files is a secondary button.
- **Copy loses the magnet text.** `CopyMagnet` gains `iconOnly`: clipboard glyph,
  `aria-label="Copy magnet link"`, tooltip, and the existing 2s `Copied` flash rendered as a
  small status span beside it (or an `aria-live` announcement when the row is dense).
  `shortMagnet` is no longer rendered in release rows; it stays for the manual-magnet form.
- **For a series with an open episode, there is one Download**, not two. The row-level
  `downloadMutation` button is not rendered; the primary is `EpisodeDownload`'s trigger,
  which already resolves the episode's file and offers "Download whole pack" inside its
  chooser. A series row with no episode selected (a season-pack search) keeps the
  release-level Download.
- **Save/unsave stays optimistic**, aria-pressed and labelled exactly as today.
- **Files** keeps opening `MagnetFiles` in place; the panel it opens inherits the 34rem
  scroll container and its action row (`Download selected`) moves to the bottom-left with the
  selection count to its right.

### Episode card

- The expanded panel is the episode card's child; no dialog and no navigation.
- Episode header row is unchanged except for the 16px row padding from T-043.

## Out of scope

Changing how releases are fetched, sorted or filtered; a dialog/sheet version of the panel;
magnet probing; the files page (T-047); playback (T-048).

## Acceptance criteria

- [ ] The find-release panel is `34rem` tall, scrolls internally and its header stays visible
      while scrolling.
- [ ] Each release row renders the filename on its own full-width line.
- [ ] The copy control is icon-only: no magnet string is rendered in a release row.
- [ ] Exactly one action row per release; Download is the only accent-styled control.
- [ ] A series with an open episode shows one Download button, not two.
- [ ] Files still opens the file list, and "Download selected" remains reachable at the
      bottom of that list.
- [ ] Keyboard order inside a row is Download → star → Files → copy.
- [ ] At 390px the action row wraps without clipping and each control stays ≥ 36px tall.

## Test plan

- `web/src/pages/__tests__/releases.test.tsx`: row structure, single Download for a series,
  copy has no text, action order.
- `web/src/components/__tests__/magnet-files.test.tsx`: file-list actions still work inside
  the taller container.
- `web/src/components/__tests__/copy-magnet.test.tsx`: `iconOnly` renders no magnet string,
  keeps the aria-label and the Copied flash.
- Manual: movie releases, series episode releases, Nyaa results, a long filename, 390px.

## Implementation notes

- `MagnetFiles` renders its own `Files` button; the release row keeps that single source of
  truth and does not add a duplicate.
- `EpisodeDownload` currently renders its trigger in its own wrapper; move the trigger into
  the shared action row rather than rendering two components that each own a button.
- The sticky header must not capture the `details`/popover from `Providers`; keep the filters
  outside the scroll container.
