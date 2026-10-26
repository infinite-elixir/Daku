# T-038 — Providers popover is clipped inside the episode panel

**Phase:** 10 · **Depends on:** T-021, T-023 · **Estimate:** S

## Goal

The release **Providers** control is a `<details>` element whose panel is absolutely
positioned inside its own container. In the title page it renders inside an expanded episode
card, and that card clips it; the release list adds a second scroll container. On a short
viewport the provider list is cut off and cannot be scrolled into view, so providers and the
anime source toggles become unreachable.

Make the panel escape ancestor clipping and stay inside the viewport at every breakpoint,
without changing the provider-selection behaviour.

## Root causes (verified)

- `TitleView.tsx` renders each episode card as
  `<li className="overflow-hidden rounded-panel ...">`; the expanded panel lives inside it.
- `ReleasesSection.tsx` renders the panel as `<div className="absolute right-0 z-10 mt-1 ...">`
  inside `<details className="relative">`.
- The release result list uses `max-h-[22rem] overflow-y-auto`, so the panel can also be
  clipped by that scroller.
- Nothing flips or clamps the panel, so on a 390 × 720 viewport (or with 10+ providers) the
  lower entries and the "Sources" section fall below the viewport.

## Scope

### Reusable popover

Add `web/src/components/Popover.tsx` (or extract the existing behaviour) with:

- a trigger `<button type="button" aria-expanded aria-controls>` instead of `<summary>`;
- panel rendered through `createPortal(..., document.body)`;
- `position: fixed` coordinates from `trigger.getBoundingClientRect()`;
- placement logic: prefer below, flip above when there is not enough room, then shift
  horizontally to stay within an 8 px viewport inset;
- `max-height: calc(100dvh - 16px)` and internal `overflow-y: auto` when the content is
  taller than the viewport;
- close on outside `pointerdown`, `Escape`, route change, and after selecting "Use defaults";
  keep it open while toggling individual checkboxes;
- return focus to the trigger on close; focus the first control on open;
- reposition on `resize` and on scroll of any ancestor (use a capture-phase scroll listener
  or close on scroll — closing is acceptable and simpler);
- no dependency on a new positioning library.

Use it for the Providers panel only. Do not redesign the filter row.

### Clipping guarantees

- The panel must not be a DOM child of the episode `<li>`, the `<details>`, or the release
  scroll container. The portal is what makes `overflow-hidden` harmless.
- Keep the episode card's `overflow-hidden`; do not remove it just to make the popover fit.
- The panel keeps `z-index` above the sticky header and release rows, and below dialogs.
- Width: `min(18rem, calc(100vw - 16px))`; on 390 px it must not cause a horizontal
  scrollbar.

## Out of scope

Redesigning the filter bar, provider search/reordering, new providers, changing what the
checkboxes do, a general dropdown framework for every menu in the app.

## Acceptance criteria

- [ ] At 390 × 720, opening Providers inside an expanded episode card shows every provider,
      both anime source toggles, "Use defaults" and any hint, with the panel scrollable when
      taller than the viewport.
- [ ] At 1440 × 900 the same panel is fully visible and anchored to the trigger.
- [ ] The panel is never clipped by the episode card or the release list, including when the
      release list has 10+ results and its own scrollbar.
- [ ] Opening near the bottom of the viewport flips the panel above the trigger; opening near
      the right edge shifts it inside the viewport.
- [ ] `Escape` closes the panel and restores focus to the Providers button; clicking outside
      closes it; toggling a checkbox keeps it open.
- [ ] Selecting "Use defaults" still clears the provider override and closes the panel.
- [ ] No horizontal scrollbar appears at 390 px, and opening the panel does not shift the
      layout.
- [ ] Existing provider/source selection and localStorage behaviour is unchanged.

## Test plan

- `web/src/components/__tests__/popover.test.tsx`: portal target, `aria-expanded`, outside
  click, Escape + focus return, scroll close/reposition.
- `web/src/pages/__tests__/releases.test.tsx`: provider panel still toggles providers and
  sources, "Use defaults" resets, and the panel is rendered outside the trigger container.
- Manual browser check at 390 × 720 and 1440 × 900 with an expanded episode and a long
  release list; note the result in the ticket. jsdom cannot verify clipping, so this check is
  part of the definition of done.

## Implementation notes

- Compute coordinates in a `useLayoutEffect` after opening so the panel measures correctly;
  clamp again after render if the panel height changed.
- Use `pointerdown` (not `click`) for outside detection so a click on a checkbox does not
  race the close handler.
- Do not lock body scroll; the panel itself scrolls.
- If the trigger scrolls out of view while open, closing is acceptable — a stale floating
  panel is worse than a reopen.
- This ticket is front-end only; no API, schema or provider changes.
