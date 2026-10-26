# T-025 — Toasts and notice polish

**Phase:** 6 · **Depends on:** T-006, T-021 · **Estimate:** S

> **Revision 2026-10-25:** the watched-state half of this ticket was replaced by **T-032
> (watch-status tracker)**. Do not add boolean `watched` columns or controls from the original
> version. This ticket now covers only the toast/notice work, which T-030 and T-032 reuse.

## Goal

Replace permanent explanatory paragraphs with a small, accessible toast system, and move
momentary success/failure copy out of the page layout. Errors that need a decision stay inline.

## Scope

### Toasts

- Add a tiny `ToastProvider` + `useToast()` in `web/src/components/`:
  - one `role="status"` / `aria-live="polite"` region for the app; errors use
    `role="alert"` / `aria-live="assertive"`;
  - success/info/error variants following STYLE.md §5.8 (max 360 px, status rule, no icons
    larger than 16 px);
  - auto-dismiss after ~4 s for success/info; errors persist until dismissed;
  - manual close button with an accessible label;
  - stacked, non-blocking, safe-area aware on mobile; no layout shift.
- The provider is deliberately tiny: no dependency, no global store beyond the provider.

### Notices to move

- Remove `<p className="text-[12px] text-fg-comment">Removing keeps files on disk.</p>` from
  `TitlePage`.
- After a successful remove, navigate to `/library` and toast:
  **"Removed <title>. Files were kept on disk."**
- Move the existing inline notices in `ReleasesSection` (saved/unsaved, queued for download,
  magnet saved/copied) to toasts.
- Keep API errors inline next to the action; do not hide them in a toast that disappears.
- The T-030 copy-magnet confirmation and T-032 status updates use this provider when it exists;
  if they land first, they may use an inline fallback and switch later.

## Out of scope

Watched state and watch statuses (T-032). Notification centre, persistence of toast history,
desktop notifications, sound, undo actions.

## Acceptance criteria

- [ ] The static "Removing keeps files on disk" text is gone.
- [ ] Removing a title shows a toast that says files were kept on disk.
- [ ] The toast is announced to screen readers and can be dismissed with the keyboard.
- [ ] Success/info toasts auto-dismiss; error toasts persist until closed.
- [ ] Save/unsave/download/magnet notices use the toast system; API errors remain inline.
- [ ] Toasts do not shift the page and respect iOS safe areas.
- [ ] The provider is reusable by T-030 and T-032 without a second implementation.

## Test plan

- `web/src/components/__tests__/toast.test.tsx`: auto-dismiss, manual close, aria-live, error
  persistence, stacking.
- `web/src/pages/__tests__/title-remove.test.tsx`: navigate + toast copy, files kept.
- `web/src/pages/__tests__/releases.test.tsx`: success notices are toasts, errors stay inline.

## Implementation notes

- Use a portal or a fixed container at the app root; do not mount one per page.
- Keep timers cleaned up on unmount and on manual close.
- Do not toast progress updates; the download UI owns those.
- Reuse the semantic tokens; no new colours.
