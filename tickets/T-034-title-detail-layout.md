# T-034 — Title detail two-column layout

> **SUPERSEDED by T-045 — do not implement.** The right-rail layout below was rejected in
> review: a page with one subject should not split its identity across three columns. T-045
> keeps the poster and identity as one object and moves overview and facts full width
> underneath. This file is retained for the phase history and for the parts of its scope
> that survive (container queries, skeleton parity, preview parity, 200% zoom).

**Phase:** 9 · **Depends on:** T-024, T-033 · **Estimate:** M · **Status:** superseded

## Goal

On a wide screen the title page packs poster, title, ratings, overview and facts into a narrow
left column and leaves most of the right half empty. Use that space: keep the identity and
overview on the left, move metadata facts into a right rail on wide viewports, and stack the
same blocks on narrow ones. This is the layout for desktop and portrait tablets that are wide
enough, not a separate mobile design.

## Scope

### Layout

- Replace the current `sm:grid-cols-[180px_minmax(0,1fr)]` title hero with a container-query
  grid on `TitleView`:

  | Container width | Layout |
  |---|---|
  | < 720 px | single column: poster, identity, ratings, overview, facts |
  | 720–1024 px | poster (160 px) + main column; facts below overview |
  | ≥ 1024 px | poster (200–220 px) + main column + **right rail** (300–340 px) |

- Use CSS container queries (`@container`) on the title panel rather than viewport queries, so
  the layout also works inside the preview and in an eventual split view. Provide a viewport
  fallback for browsers without container queries.
- Right rail contains, in order:
  1. `RatingsRow` (keep IMDb/RT/Metacritic together);
  2. `MetadataFacts` — genres, runtime, studios, directors, writers, cast;
  3. a compact **Availability / Progress** block (saved releases, episodes watched, file
     status) when available.
- Keep in the main column: kind badge, title, year/IDs, wanted + status controls, overview
  (`ExpandableText`), primary actions, and the **Episodes / Releases** section full width below
  the hero.
- Do not duplicate facts in both columns. On narrow screens the rail contents stack in the main
  flow after the overview at the same DOM order as the wide layout (CSS grid reorders visually,
  DOM stays logical for screen readers).
- The right rail may be sticky within the hero (`top: 72px`) on tall screens; respect reduced
  motion (no transition) and do not let it overlap the episodes section.

### Fill and rhythm

- The content shell stays `max-width: 1440px`, but the title hero must use the full width: no
  fixed narrow text column with a huge empty gutter.
- Overview prose is capped at `68ch`; the right rail is allowed to be narrower. The episode and
  release panels span the full width.
- Gaps follow the 4 px grid: 24 px between columns, 16 px between rail blocks.
- At 1440 px the poster must not stretch beyond 220 px; artwork stays 2:3.
- At 1024–1199 px, reduce the rail to 280 px and the poster to 180 px rather than dropping the
  rail immediately.

### States

- Loading skeleton mirrors the three-column layout so there is no shift when data arrives.
- Missing metadata fields collapse the rail block; an empty rail is not rendered.
- The preview (`/preview/...`) uses the same component and therefore the same layout; it must
  not show library-only blocks (no saved/downloads counts).
- Error states stay full width, not inside the rail.
- The page remains usable with 200% zoom (effective ~720 px) — no horizontal scroll.

## Out of scope

Banner/dither art (T-035), a full navigation redesign, moving episodes into the rail, a
persistent sidebar for related titles, and a separate desktop component tree.

## Acceptance criteria

- [ ] At ≥ 1024 px container width the title page shows poster, main identity column and a right
      rail with ratings and facts, filling the available width.
- [ ] At 390 px and 820 px the same content stacks in a readable order with no duplication and
      no horizontal scroll.
- [ ] Portrait tablets (e.g. 1024×1366) get the rail; a narrow split view does not.
- [ ] Ratings appear once, in the rail on wide screens and under the title on narrow ones.
- [ ] The episodes/releases section spans the full width below the hero.
- [ ] The skeleton reserves the same three regions and does not cause layout shift.
- [ ] Screen-reader order matches the visual reading order; the rail is a labelled
      `aside`/`section`, not a pile of divs.
- [ ] The preview uses the same layout without library-only counts.
- [ ] 200% zoom at 1440 px still does not scroll horizontally.
- [ ] STYLE.md §4 documents the title-page grid and the container breakpoints.

## Test plan

- `web/src/components/__tests__/title-view.test.tsx`: three-column class contract at wide,
  stacked at narrow, no duplicate facts, preview variant.
- `web/src/pages/__tests__/title-page.test.tsx`: skeleton regions, full-width releases section.
- Manual: 390, 820, 1024, 1200, 1440 px; 200% zoom; keyboard and screen-reader order.

## Implementation notes

- Keep one DOM tree. Do not render a desktop copy and hide it with CSS; duplication breaks
  screen readers and doubles query cost.
- Move `RatingsRow` into the rail on wide screens via grid placement, not a second component
  instance.
- Container queries are supported in current Chromium/Firefox/Safari; the fallback is a
  viewport media query with the same breakpoints.
- The episode/release panels are already full width; do not wrap them in the hero grid.
- Update the skeleton in `TitlePage.tsx` at the same time; a stale skeleton is a visible bug.
