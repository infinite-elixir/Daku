# T-018 — PWA, search-as-you-type, responsive polish

**Phase:** 5 · **Depends on:** T-006, T-011 · **Estimate:** M

## Goal

Make Daku feel like an app on a phone: installable, fast, thumb-friendly, and honest about
what is happening.

## Scope

- `manifest.webmanifest`, icons (192/512/maskable), `theme-color`, `display: standalone`,
  iOS meta tags, safe-area insets.
- Service worker: cache the app shell only; never cache `/api/*`; offline page.
- Search page: 300 ms debounce, request cancellation, recent searches (localStorage),
  "Add" optimistic with rollback.
- Global: skeleton loaders, error boundary, toast for failures, pull-to-refresh disabled
  (browser default), 44 px targets, bottom nav with active state.
- Empty states for search, library, downloads, telegram inbox.
- "Install Daku" prompt where supported; a "Add to Home Screen" hint on iOS Safari.
- Accessibility: labels, focus rings, contrast ≥ 4.5:1, respects `prefers-reduced-motion`.

## Out of scope

Offline media, push notifications, dark/light theme switch beyond `prefers-color-scheme`.

## Acceptance criteria

- [ ] Lighthouse mobile PWA installability passes (manifest, icons, start_url, display).
- [ ] On Android Chrome, "Add to Home Screen" opens standalone with no browser chrome.
- [ ] The app shell loads in under 2 s on a throttled 3G profile after first visit.
- [ ] `/api/*` responses are never served from cache.
- [ ] Search input is usable at 390 px with the keyboard open; results remain scrollable.
- [ ] Every destructive action asks for confirmation; every failed action shows a toast with
      a retry where possible.
- [ ] No horizontal scroll at 320 px or 1440 px on any screen.
- [ ] An offline load shows a clear "Daku is offline" page, not a browser error.
- [ ] `prefers-reduced-motion` disables all non-essential animation.

## Implementation notes

Use `vite-plugin-pwa` in `injectManifest` mode with a tiny custom service worker:
precache the build assets, network-first for navigation, network-only for `/api`.

Recent searches: max 10, stored under `daku.recentSearches`, cleared from settings.

Optimistic add: `onMutate` inserts a placeholder title into the library cache, `onError`
rolls back and toasts.

Do not add a component library for this; Tailwind + a handful of primitives is enough.

## Test plan

- `web/src/__tests__/search.test.tsx`: debounce, cancel, optimistic add, rollback.
- `web/src/__tests__/offline.test.tsx`: shell renders offline page.
- Manual: Lighthouse mobile, 320/390/768 px widths, iOS Safari add-to-home.
