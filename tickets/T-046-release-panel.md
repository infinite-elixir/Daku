# T-046 — Episode master–detail and three-level release navigation

**Phase:** 12 · **Depends on:** T-030, T-031, T-043, T-045 · **Estimate:** L · **Supersedes:** the
original T-046 "taller panel, one action row" draft

## Problem

Expanding an episode renders `ReleasesSection` in `mode="find"` inside the episode card, with
`max-h-[22rem] overflow-y-auto`. That is one and a half releases tall, so the panel is scrolled
before the user has read a filename. Inside a release row, controls are scattered across two
clusters:

- `CopyMagnet` sits inline with the filename and prints `magnet:?xt=urn:btih:1234…` next to the
  button — noise nobody reads and a truncated string nobody can verify;
- the star, `Download` and the `Files` button live in a different cluster at the end of the
  first line;
- for series, `EpisodeDownload` adds a *fourth* button (`Download S01E03`) below the row, next
  to a fifth (`Download`) at the top. Nothing explains the difference between them.

Saved releases for a series also live in a separate title-level block fed by the same open
episode, so one episode's releases are split across two unrelated places on the page.

## Goal

Clicking an episode opens a dedicated release menu instead of expanding a short box inside the
card. On wide screens the episode list and the menu sit side by side; on narrow screens the
menu replaces the list, drill-down style. Inside the menu, a release row is a navigation
target, and a release's options (download, save, copy) have their own sub-menu.

Three levels, one job each:

1. **Episode list** — pick the episode.
2. **Release list** — compare torrents for that episode (Find / Saved).
3. **Release options** — everything you can do with one release.

This ticket owns levels 1–2, the level-3 shell and the history/back behaviour. T-056 completes
level 3 with the collapsible file list and dead-release actions.

## Layout

```
≥ 900px container                         < 900px container
┌───────────────────┬────────────────────┐   ┌────────────────────────────┐
│ [S1] [S2]         │ ← Episodes (mobile │   │ [S1] [S2]                  │
│ S01E01 … ●        │   only)            │   │ S01E01 … ●                 │
│ S01E02 …          │ S01E03 · name      │   │ S01E02 …                   │
│ S01E03 … ◀ selected│ [Available] [Play]│   │ S01E03 … ◀ selected         │
│                   │ Find | Saved (2)   │   └────────────────────────────┘
│                   │ quality · sort …   │   click → level 2 replaces the list
│                   │ ┌────────────────┐ │   ┌────────────────────────────┐
│                   │ │ filename        │ │   │ ← Episodes                  │
│                   │ │ 1080p · 2.1 GB …│ │   │ S01E03 · name               │
│                   │ │ hints · saved › │ │   │ Find | Saved (2)            │
│                   │ └────────────────┘ │   │ …release rows…              │
│                   │ Showing 20 of 41   │   └────────────────────────────┘
└───────────────────┴────────────────────┘   click a row → level 3
```

- Two columns at container width ≥ 900px: episode list `minmax(280px, 360px)`, release pane
  `minmax(0, 1fr)`, gap `--spacing-gutter`.
- Below 900px: one column. The pane replaces the episode list; a `← Episodes` button returns.
- With no episode selected at ≥ 900px, the pane shows "Pick an episode to see its releases."
  No release query fires until an episode is selected.
- The selected episode stays in `?season=&episode=`; refresh and deep links land on its release
  list. Level 3 is local state (see below).
- The episode row keeps its current season tabs, eye/watched control, status pill and
  "Season actions" menu. The selected row is marked (`aria-current="true"` plus the existing
  accent border) whether or not the pane is open.
- `renderEpisodeExpanded` and the inline `episode-panel-*` container are removed. Nothing
  renders inside an episode row.

## Level 2 — release list

Fixed above the scroll area:

- Episode header: `S01E03 · Episode name`, status pill, and `Play` targeting **that episode's**
  file (disabled with a reason when it has none). No second watched toggle.
- `Find releases | Saved (n)` tabs, mirroring the movie page.
- The existing filters (`Minimum quality`, `Sort`, `Providers`, `Reset`), unchanged in
  behaviour.

Scrolling area:

- The existing results line (`Showing N of M`, quality counts, degraded/stale badges) is
  sticky at the top of the scroll container.
- Release rows, three lines, fixed order:
  1. **Filename** — full width, one line, `text-body`, truncate with a `title` attribute.
     Nothing else on this line.
  2. **Meta** — `quality · size · seeders · SOURCE`, plus the existing `PackBadge`;
     `hintsLine` stays a fourth muted line when present.
  3. **Status** — a saved-star indicator when saved, and a chevron. No buttons.
- The whole row is a button that opens level 3. Keyboard focus lands on the row.
- Saved rows in the Saved tab use the same row and open the same level 3. Dead/checked/episode
  info stays on the row; "Find another" moves to level 3 in T-056.
- The paste-a-magnet form moves under the Saved tab, alongside the saved list.
- Pane height: `42rem` cap on narrow screens; on wide screens the pane clamps to the viewport
  (`calc(100dvh - …)`), scrolls internally and sticks under the app header.
- Find/Saved tab resets to **Find** when the episode changes; filters carry over between
  episodes for the session (Reset is the only way back to defaults). Scroll resets to the top
  on episode change.

## Level 3 — release options shell

- Rendered in the same pane, replacing the list; `← Releases` returns.
- Entering level 3 pushes one history entry. `popstate` (system/Android back) and
  `← Releases` both step back to the list without leaving the title page. Leaving the title
  page still takes one more back.
- Content at T-046 minimum:
  - release identity: filename, `quality · size · seeders · SOURCE`, `PackBadge`, hints;
  - `Download S01E03` as the only primary, using today's `EpisodeDownload` confirm → chooser
    flow. "Download whole pack" stays inside the chooser behind its checkbox;
  - `★` save/unsave, optimistic, `aria-pressed`, labelled exactly as today;
  - icon-only `CopyMagnet` (`iconOnly`): clipboard glyph, `aria-label="Copy magnet link"`,
    tooltip, 2s `Copied` flash as an `aria-live` status; no magnet string is rendered;
  - for a dead saved release, a `Find another` action (details in T-056);
  - `Files` list (auto-opened when the options screen appears, collapsible) with `Download selected`.
- Returning to the list restores the active Find/Saved tab, the scroll position and marks the
  release just viewed.
- A series release with an open episode has exactly **one** Download control in the whole
  flow. A release row never renders a download button.

## Title page

- Remove the series `ReleasesSection mode="saved"` block and its heading. Saved releases for an
  episode live on that episode's Saved tab.
- Keep a small "Saved, no episode" section at the bottom of the title page, rendered only when
  saved rows with `episode_id = null` exist. It reuses the level-3 options screen.
- Movies keep today's inline `Find releases | Saved` tabs and the manual-magnet form, but adopt
  the new three-line row and one action row: `[Download] [★] [copy] [Files]`, with Files
  expanding the file list inline as today. Download remains the only accent-styled control.

## Out of scope

Changing how releases are fetched, sorted, filtered or deduped; a new route or URL for an
episode or a release; a dialog/sheet version of the panels; magnet probing; the downloads page
(T-044); the files page (T-047); playback beyond the per-episode Play button (T-048);
collapsible Files and dead-release actions (T-056).

## Acceptance criteria

- [x] At container ≥ 900px the episode list and release pane render side by side; below 900px
      the pane replaces the list with a `← Episodes` button.
- [x] No release request fires until an episode is selected; the empty pane explains what to do.
- [x] Clicking an episode opens its release menu; the selected row is marked and the season tab
      is preserved.
- [x] Each release row is a single navigation target with the filename on its own full-width
      line, a meta line, an optional hints line, a saved indicator and a chevron — no buttons.
- [x] Level 3 opens in the pane; `← Releases` and system back both return to the list.
- [x] Level 3 shows one primary `Download S01E03` and no magnet string; copy is icon-only with
      an accessible label.
- [x] Returning from level 3 restores the Find/Saved tab and scroll position and marks the row.
- [x] Switching episodes resets the tab to Find and the scroll to top; filters persist for the
      session.
- [x] The release pane header, tabs, filters and results line stay visible while the list
      scrolls; the pane is `42rem` tall on narrow screens and viewport-clamped on wide ones.
- [x] Play in the episode header launches the selected episode's file, not the next episode.
- [x] Saved releases open the same level 3; the manual magnet lives on the Saved tab.
- [x] The series title-level Saved block is gone; "Saved, no episode" appears only when an
      unassigned row exists.
- [x] Movies keep the inline Find/Saved tabs and Files toggle, with the new row structure and a
      single accent-styled Download.
- [ ] At 390px the drill-down works without clipping and every control stays ≥ 36px tall.
      *(Manual browser pass not run in this environment.)*

## Implemented

`TitleView` now wraps the episodes section in an `.episode-master-detail` container and renders
the episode list beside `renderEpisodePane` at container ≥ 900px; below that the list hides as
soon as an episode is selected and the pane shows a `← Episodes` button. The inline
`episode-panel-*` expansion is gone. `ReleasesSection` gained `mode="episode"`: an episode
header with the episode's own Play target, Find/Saved tabs, the filters, a sticky results line
inside a scrolling `42rem`/viewport-capped list, and pure-navigation release rows. Level 3 is a
local-state options view with a pushed history entry, `← Releases`, release identity, one
`Download S01E03` through the existing `EpisodeDownload` confirm/chooser flow, an optimistic
star and icon-only copy. The series title-level Saved block was replaced by a conditional
"Saved, no episode" section. Movie rows kept their tabs but moved to the stacked row layout,
icon-only copy and one accent Download.

Tests: `web/src/pages/__tests__/releases.test.tsx` (episode row navigation, single primary,
no magnet text, manual magnet on Saved), plus the existing title-page/episode suites.
`pnpm -C web exec tsc --noEmit`, `pnpm -C web test` and `pnpm -C web build` pass.

Deferred to T-056: dead-release "Find another" and the final file/chooser
synchronisation. The 390px browser pass remains manual.

## Test plan

- `web/src/pages/__tests__/title-episodes.test.tsx`: master–detail vs drill-down (container
  width mocked), empty pane, no fetch before selection, deep link `?season=1&episode=1`
  still fetches that episode, back returns to the marked row.
- `web/src/pages/__tests__/releases.test.tsx`: row structure, pure-navigation row, Find/Saved
  tabs, filter carryover, tab reset on episode switch, no duplicate Download.
- `web/src/components/__tests__/release-options.test.tsx`: level 3 opens in place, one primary,
  copy is icon-only, history push/pop.
- `web/src/components/__tests__/copy-magnet.test.tsx`: `iconOnly` renders no magnet string,
  keeps the aria-label and the Copied flash.
- `web/src/pages/__tests__/title-page.test.tsx`: series Saved block removed, unassigned section
  conditional, movie inline tabs intact.
- Manual: movie releases, series episode releases, saved/dead releases, long filename, 390 /
  900 / 1440px, keyboard pass, system back on a phone.

## Implementation notes

- Detect the 900px breakpoint on the episodes section, not the viewport: a small
  `ResizeObserver` hook with a safe fallback (treat as wide when the API is unavailable, so
  tests do not render a one-column tree by accident). Keep one DOM tree; do not fork desktop
  and mobile components.
- Use the existing `?season=&episode=` params and `selectionFromParams`. Do not add a route.
- For level 3 history, push a state entry when opening the options view and listen for
  `popstate` to close it. Do not put the release in the URL.
- Keep TanStack Query keys unchanged so cached results survive drill-down; `keepPreviousData`
  already prevents jumps on Show more.
- `EpisodeDownload` currently owns its trigger and its confirm/chooser markup. At level 3,
  render its trigger as the pane's primary; do not render two components that each own a
  button. Its pack/chooser flow is unchanged.
- `MagnetFiles` owns its own trigger; level 3 renders it with `defaultOpen`, so the file list
  is the single source of truth and loads as soon as a release is opened.
- The sticky header must not capture the `Providers` `<details>` popover: keep the filters
  outside the scroll container.
- Keyboard order inside a row: the row is one tab stop; inside level 3:
  `Download → ★ → copy → Files`.

## Follow-up

T-056 completes level 3: dead-release
"Find another", and the final saved-row/pack-flow polish.
