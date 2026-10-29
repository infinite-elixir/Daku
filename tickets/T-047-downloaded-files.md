# T-047 — Downloaded files: carousel, files page, thumbnails

**Phase:** 12 · **Depends on:** T-014, T-043, T-044, T-045 · **Estimate:** L

## Problem

Daku knows which files it filed, but the title page reduces them to `Episodes 10/10` and a
count of saved releases, and the library reduces them to an availability pill. The Downloads
page lists `downloaded_files` as text chips. Nothing shows the actual files, their size, or
what they look like, and there is no way to reach a single file on its own.

A title with a finished season is the most common state in this app, and it is the least
legible one.

## Goal

Show what is on disk as media: a season-aware strip of file cards directly above Releases, a
full grid page for the whole title, real thumbnails with a poster fallback, and a library
filter that finds downloaded titles.

## Scope

### 1. Files API

`GET /api/titles/{id}/files`

```json
{
  "title_id": "...",
  "file_count": 10,
  "total_size_bytes": 21474836480,
  "seasons": [{ "library_season": 1, "file_count": 10, "total_size_bytes": 21474836480 }],
  "items": [
    {
      "id": "...",
      "download_id": "...",
      "episode_id": "...",
      "episode_label": "S01E03",
      "episode_name": "The Jacket",
      "season": 1,
      "episode": 3,
      "file_name": "Cyberpunk.Edgerunners.S01E03.1080p.mkv",
      "size_bytes": 1476395008,
      "is_primary": true,
      "has_episode": true
    }
  ]
}
```

- Read-only, no new table, no schema change. Sorted by season, episode, then filename.
- `seasons` only includes seasons with at least one file.
- `has_episode: false` covers samples and extras; they are returned but the UI groups them
  separately.
- 404 `TITLE_NOT_FOUND` for an unknown id.

`GET /api/media-files/{id}/thumbnail`

- Resolves `lib_path` (falling back to `src_path`), requires the path to exist.
- `ffmpeg -ss <0.25 × duration_s> -i <path> -frames:v 1 -vf scale=480:-2 -q:v 4`
  via `asyncio.create_subprocess_exec`, no shell, fixed argv.
- Cache: `data_dir/thumbnails/{media_file_id}.jpg`, served with `ETag` (`id:size:mtime`) and
  `Cache-Control: private, max-age=86400`.
- Miss because the file is gone or ffmpeg fails → `404 THUMBNAIL_UNAVAILABLE`; never a 500,
  never a partial JPEG.
- 10s timeout per extraction; at most 2 concurrent extractions per process; failures cached
  in memory 5 minutes so a broken file is not retried on every scroll.
- `ffmpeg` missing → `503 THUMBNAIL_UNAVAILABLE`, logged once with `ffmpeg not found`.
- Log the media-file id and exit code only. Never log the path.

`GET /api/titles` gains `has_files: bool` (any `media_files` row) and each item gains
`file_count`. `availability` keeps its three existing values.

Doctor: new `ffmpeg` line, `ok | unavailable`, checked with `shutil.which` and never by
running an extraction.

### 2. `MediaFileCard`

One component used by the carousel and the files page.

- 16:9 thumbnail (`thumbnail_url` with poster fallback, then a kind-coloured placeholder),
  radius 8px, `loading="lazy"`, `decoding="async"`.
- Line 1: `S01E03` (mono) + episode name, truncated. Extras show the filename.
- Line 2: size, `--text-muted`.
- The card is a link to the title page's media-file anchor for now; T-048 turns the click
  into an mpv launch. **No Jellyfin play URL is rendered** — the mpv decision replaces it.
- Keyboard focusable, `focus-visible` ring, whole card target ≥ 44px tall.

### 3. Download carousel (`DownloadedFiles`)

- Rendered on `TitleView` after the episodes section and before Releases (movies: after
  facts, before Releases).
- Header: `Downloaded` + `N files · 20.0 GB` and a `View all files (N)` link to
  `/title/{id}/files`.
- **Season pills** above the strip, only when more than one season has files. Default
  selection follows the open episode tab's season, else the newest season with files. Pills
  filter the strip only; the header count stays the title total.
- Horizontal strip with scroll-snap, 6 cards at 1440px, 2.5 at 390px, arrow-key scrolling,
  no autoplay, no drag inertia hacks.
- Extras are **hidden** here.
- Empty (no files at all): the section is not rendered.
- Above `--spacing-section` from the neighbouring sections.

### 4. Files page

- Route `/title/{id}/files`, inside `AppShell`, reachable from:
  - `View all files (N)` in the carousel header,
  - `Files (N)` in a Downloads group header,
  - the `N files` link in a library card meta row.
- Header: back link to the title, title name, `N files · total size`.
- Season tabs, same rule as the carousel (only seasons with files), plus an `Extras` group
  after the season grid when any file has `has_episode: false`.
- Responsive grid: 2 / 4 / 6 columns (mobile / tablet / desktop), `--spacing-panel` gaps.
- Thumbnails lazy via `IntersectionObserver`, one request per visible card, no request for
  cards off screen.
- Empty state: "No files yet" + link back to the title.
- Error state: message + Retry, full width.
- 200% zoom at 1440px: no horizontal scroll.

### 5. Library

- New `Downloaded` filter chip, `aria-pressed`, sending `has_files=true`.
- **Mutually exclusive with `No file yet`**: toggling one clears the other, because they are
  the two ends of one axis.
- `LibraryCard` restructured so the `N files` link is not inside the poster anchor: the card
  becomes a container with the poster + title as the link, and the meta row (kind badge,
  availability pill, files link) as a sibling. Files link hidden when `file_count` is 0.
- No nested anchors anywhere; verify with the DOM assertion in the test.

## Out of scope

Playing files (T-048), deleting a file from the UI, re-inspecting metadata from the page,
thumbnails for downloads that are still running, virtualising a 500-file grid (measure
first), Jellyfin artwork, trickplay, a poster proxy.

## Acceptance criteria

- [ ] `GET /api/titles/{id}/files` returns every media file with size, episode label and
      season, only files that exist, and per-season aggregates.
- [ ] `GET /api/media-files/{id}/thumbnail` returns a JPEG for a real file, caches it on disk,
      and sends an ETag that yields a 304 on the second request.
- [ ] A missing file or a failing ffmpeg returns `404 THUMBNAIL_UNAVAILABLE` and the card
      falls back to the poster.
- [ ] The carousel appears only when files exist, shows season pills only when more than one
      season has files, and hides extras.
- [ ] `View all files (N)` opens `/title/{id}/files`, which renders every file for the title
      with season tabs and an Extras group.
- [ ] Thumbnails are requested only for visible cards; scrolling a 100-file grid does not
      fire 100 simultaneous extractions.
- [ ] The `Downloaded` chip filters to titles with at least one file and clears `No file yet`.
- [ ] A library card exposes a `N files` link without nesting it inside the card anchor.
- [ ] No Jellyfin play URL is rendered anywhere.
- [ ] Doctor reports ffmpeg as `ok` or `unavailable` without running an extraction.

## Test plan

- `tests/routers/test_title_files.py`: list shape, ordering, extras flag, 404, empty title.
- `tests/services/test_thumbnails.py`: cache hit, ETag/304, missing binary, missing source
  file, timeout, concurrency cap.
- `tests/routers/test_titles.py`: `has_files` filter and `file_count` aggregate.
- `web/src/pages/__tests__/title-files.test.tsx`: grid, season tabs, extras group, empty and
  error states, lazy loading.
- `web/src/components/__tests__/downloaded-files.test.tsx`: pills only for multi-season
  titles, default season follows the tab, extras hidden, `View all` link.
- `web/src/pages/__tests__/library.test.tsx`: chip exclusivity, files link outside the anchor.
- `web/src/pages/__tests__/downloads.test.tsx`: group header Files link.

## Implementation notes

- Use `duration_s` from `media_files` when present; do not run ffprobe again for a thumbnail.
  Fall back to `-ss 60` clamped to the file's length when duration is unknown.
- Name cache files by media-file id, not by path: paths move, ids do not.
- Reuse the existing `doctor` line style; a thumbnail failure must never mark the whole
  integration unhealthy, only `ffmpeg`.
- The files page is a read-only view. Do not add remove/delete here; that is the downloads
  queue's job (T-039).
- `MediaFileCard` must accept a `thumbnail_url` that 404s without breaking layout; reserve the
  16:9 box before the image loads.
