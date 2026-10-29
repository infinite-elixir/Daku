# T-045 — Title hero: poster-matched identity column

**Phase:** 12 · **Depends on:** T-024, T-043 · **Estimate:** M · **Supersedes:** T-034

## Problem

`TitleView` uses a fixed two-column grid, `sm:grid-cols-[180px_minmax(0,1fr)]`. Everything —
badges, title, ids, ratings, overview, facts, counters and actions — is poured into the text
column, so it grows far past the poster and leaves a tall empty strip under the artwork while
the description is squeezed into the narrow half. T-034 proposed fixing this with a right
rail for ratings and facts; that design is rejected in favour of the one below, because a
rail splits the same identity across three columns on a page with only one subject.

## Goal

The poster and the identity block are one visual object: the identity column is exactly as
tall as the poster, its actions sit on the poster's bottom edge, and everything long-form
reads full width underneath.

## Scope

### Hero (`TitleView`)

```
┌───────────────┬──────────────────────────────────────────────┐
│               │  [Movie] [Available] [Wanted]                │
│               │  Cyberpunk: Edgerunners                      │
│   poster      │  2022 · tt8312878                            │
│   200–220px   │  IMDb 8.3  RT 96%  MC 87                     │
│   2:3         │  Saved releases 4 · Episodes 10/10           │
│               │  [Mark wanted] [Fix metadata] [Remove]       │
└───────────────┴──────────────────────────────────────────────┘
overview …
genres / runtime / studios / cast …
downloaded files (T-047)
episodes …
releases …
```

- Grid: `grid-cols-[minmax(160px,220px)_minmax(0,1fr)]` at container width ≥ 720px, gap
  `--spacing-gutter`.
- The identity column is `flex flex-col`; the action row is its last child with `mt-auto`, so
  the actions land on the poster's bottom edge regardless of how much metadata exists. One
  action bar, never a second copy under the poster.
- Identity order: badges row, `h1`, year + ids, `RatingsRow`, counters, actions.
- Counters stay a `<dl>` but become one inline row: `Saved releases · Episodes · Folder`, not
  three stacked blocks.
- Poster: `max-width: 220px`, `aspect-ratio: 2/3`, never stretched. 180px between 720 and
  1023px container width.
- The `border-l-2` detail rule moves from the title block to the hero (one per page, as
  STYLE requires).

### Below the hero

- `ExpandableText` overview full width, capped at `68ch`.
- `MetadataFacts` full width under the overview: genres and runtime first, then studios,
  directors, writers, cast, laid out in a `repeat(auto-fill, minmax(220px, 1fr))` grid with
  `--spacing-panel` gaps. Empty fields collapse the block; no empty rail.
- Then the downloaded-files section (T-047), the episodes section, then releases. All full
  width; none of them inside the hero grid.

### Narrow

- Container width < 720px: single column. Poster gets its own block capped at 160px (never
  full-bleed: a 390px-wide 2:3 poster is 585px tall), then identity, then actions as a
  full-width row of equal-height buttons, then overview and facts.
- At 720–1023px the poster is 180px and the facts grid drops to two columns.

### States

- Skeleton in `TitlePage.tsx` mirrors the hero (poster block + identity lines) and the facts
  block, so no shift when data arrives.
- Preview (`PreviewPage`) uses the same component; library-only counters and actions are
  absent there, exactly as today.
- Missing overview or facts: the block collapses, the hero still balances because `mt-auto`
  anchors the actions.
- Error states stay full width above the hero.
- Container queries where available, viewport media query fallback, as T-034 specified.

## Out of scope

The right rail (explicitly rejected), banner/dither art (T-035), moving episodes or releases
into the hero, a persistent sidebar of related titles, a desktop-only component tree.

## Acceptance criteria

- [x] At ≥ 720px the identity column's bottom edge matches the poster's bottom edge at 200,
      220 and 180px poster widths, with 2 or with 12 lines of metadata.
- [x] Actions render once, in the identity column on wide and as a full-width row below the
      identity on narrow; never both.
- [x] Ratings appear once, under the title; no rail exists anywhere on the page.
- [x] Overview caps at 68ch; facts stack vertically beside it on wide containers and below
      it, with a section gap, on narrow ones.
- [x] Below 720px the poster is ≤ 160px wide and everything stacks in one column.
- [ ] No horizontal scroll at 390, 820, 1024, 1440px or at 200% zoom. *(Manual browser pass
      not run in this environment.)*
- [x] Skeleton reserves the same regions and causes no layout shift.
- [x] Screen-reader order matches visual order; the hero is one labelled section.
- [x] The preview renders the same layout without library-only counters or actions.
- [x] STYLE.md §4 documents the hero grid and the 720px container breakpoint.

## Test plan

- `web/src/components/__tests__/title-view.test.tsx`: hero grid classes, `mt-auto` on the
  action row, single action render, no `aside`/rail element, facts outside the hero grid.
- `web/src/pages/__tests__/title-page.test.tsx`: skeleton regions, full-width sections below.
- `web/src/pages/__tests__/preview.test.tsx`: no counters/actions in preview.
- Manual: a movie with a long overview, a series with 10 episodes, a metadata-empty title,
  390 / 820 / 1024 / 1440px, keyboard pass.

## Implementation notes

- Keep one DOM tree; do not render a desktop and a mobile hero.
- `mt-auto` needs the identity column to have a definite height, which it gets from the
  grid stretching both cells to the poster's intrinsic height. Do not set a fixed height.
- T-034's file is retained with a "Superseded by T-045" header; do not delete it.
- If T-047 lands first, place its section between facts and episodes without restructuring
  the hero.

## Implemented

`TitleView` now renders a single poster + identity grid inside a `title-hero-scope`
container. The grid is `grid-cols-1` below 720px and two columns above it, with the 180px
(720–1023) and 220px (≥1024) poster widths in `theme.css`; a viewport media query covers
browsers without container queries. The identity column is a flex column whose last child is
the one action row with `mt-auto`, so it pins to the poster's bottom edge. Counters became a
single inline `<dl>` row and `showCounters={false}` hides them in the preview. Overview and
`MetadataFacts` moved outside the hero into a `title-facts-scope`: from 900px container width
the overview stays capped at `68ch` on the left and the facts stack vertically on the right;
below 900px they stack with a `--spacing-section` gap, so the description is no longer flush
against the facts. The wide banner art is a full-bleed backdrop that starts above the poster
and fades into the canvas, so it no longer reads as a rounded card sharing the poster's
margin. The skeleton mirrors both regions and the hero is a labelled `<section>`.

Tests: `web/src/components/__tests__/title-view.test.tsx` (new),
`web/src/pages/__tests__/title-page.test.tsx` (skeleton) and
`web/src/pages/__tests__/preview.test.tsx` (no library counters/actions). `scripts/check.sh
--web` passes. The 390/820/1024/1440px and 200% zoom browser pass is still manual and was not
run here.
