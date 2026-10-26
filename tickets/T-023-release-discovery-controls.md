# T-023 — Release discovery: minimum quality, providers, sort, show more

**Phase:** 6 · **Depends on:** T-007 · **Estimate:** M

## Goal

Make the release list show what Torrentio actually has. Today a 2160p release is usually
present but invisible: the API sorts by seeders, slices to `limit=20`, and the default
provider set is narrower than tsearch's. Add explicit controls instead of relying on luck.

Verified 2026-10-23 with Dune Part Two (`tt15239678`):

- Daku defaults → 50 streams, 18×2160p, 28×1080p, 4×720p.
- tsearch defaults → 78 streams, 31×2160p (adds `torrentgalaxy`, drops `unknown`).
- The first 20 by seeders are mostly 1080p, which is why 2160p "disappears".

## Scope

### Config and defaults

- Align `TORRENTIO_DEFAULT` with tsearch:
  `providers=yts,eztv,1337x,thepiratebay,torrentgalaxy|qualityfilter=scr,cam,unknown|sort=seeders`.
- `settings.quality_filter` default becomes `["cam", "scr", "unknown"]`.
- Add `providers` to `Settings` (comma string or list) so the default provider set is not
  buried inside `torrentio_config`; keep `torrentio_config` as the override of last resort.
- Update `AGENTS.md` §4 and §6 when this lands.

### API

`GET /api/titles/{id}/releases` gains:

- `providers` — comma-separated list. Trim, lowercase, deduplicate. Empty means "use the
  configured default". Validate the character set (`^[a-z0-9._-]+$`, max 20 entries); return
  `422 INVALID_PROVIDERS` for junk. Do not maintain a closed whitelist — Torrentio adds
  providers and a stale list would be worse than a passthrough.
- `min_quality` — `480p|576p|720p|1080p|1440p|2160p`. Keep releases whose quality rank is
  **greater than or equal to** it. `unknown` is excluded whenever a minimum is set. Omitted
  or empty means "any".
- `sort` — existing `seeders|quality|size`.
- `limit` — raise the ceiling to 200. `offset` is not added; Torrentio returns one payload and
  the UI grows the limit in steps.

`GET /api/releases/options` returns:

```json
{
  "providers": ["yts", "eztv", "1337x", "thepiratebay", "torrentgalaxy"],
  "quality_labels": ["2160p", "1440p", "1080p", "720p", "576p", "480p"],
  "sorts": ["seeders", "quality", "size"],
  "defaults": {"providers": [], "min_quality": null, "sort": "seeders", "limit": 20},
  "limit_steps": [20, 50, 100, 200]
}
```

The response for a search gains `total_unfiltered`, `returned`, `quality_counts`
(`{"2160p": 18, ...}` after filtering) and echoes `providers` / `min_quality`. Keep `total`
as the matched count for existing callers.

Behaviour order, documented in code:

1. parse all streams;
2. dedupe by lowercased `info_hash`, keeping the row with the most seeders and recording
   `duplicates_removed`;
3. drop labels in `quality_filter` (cam/scr/unknown by default);
4. apply `min_quality`;
5. sort (stable ties);
6. slice to `limit`.

The Torrentio prefix changes with `providers`; the raw-payload cache key is the URL, so
different provider selections must not share a cache entry. Add `with_providers(config, list)`
next to `with_qualityfilter`.

### UI (`ReleasesSection`, Find releases tab)

A controls row above the results:

- **Minimum quality** — Any / 480p / 720p / 1080p / 1440p / 2160p.
- **Sort** — Seeders / Quality / Size.
- **Providers** — a popover with checkboxes (`yts`, `eztv`, `1337x`, `thepiratebay`,
  `torrentgalaxy`). "All" is the configured default; "None" is rejected with a hint. The
  selection is remembered in `localStorage` under `daku.releaseProviders` and sent per
  request. It is not written to the server config.
- Count line: `Showing 20 of 78 · 2160p 18 · 1080p 28` (from `quality_counts`).
- **Show more** appends the next step from `limit_steps`; it stays disabled when
  `returned >= total`.
- **Reset** restores the configured defaults.
- Empty state names the active minimum/providers and offers Reset; do not say "no releases"
  when the filters are the reason.

The Saved tab keeps its existing quality/hide-dead controls; do not merge the two.

## Out of scope

New release providers beyond Torrentio. Auto-download rules. Changing the `Release` dataclass
shape. Per-episode saved-release grouping.

## Acceptance criteria

- [x] With Daku defaults, Dune Part Two returns at least one 2160p release in the first page
      when `min_quality=2160p` and `sort=quality`.
- [x] Default results include `torrentgalaxy` and no longer include `unknown` releases.
- [x] `min_quality=1080p` returns only 1080p/1440p/2160p rows, never unknown or cam/scr.
- [x] Provider toggles change the Torrentio prefix and therefore the cached URL; switching
      back reuses the correct cache entry.
- [x] Duplicate `info_hash` rows across providers collapse to one, with no loss of the
      highest seeder count.
- [x] `limit=20` returns 20 rows and reports the full matched total; Show more raises the
      limit without losing the current selection or scroll position.
- [x] Provider selection survives a reload via `localStorage`; an empty selection cannot be
      submitted.
- [x] `quality_counts` matches the filtered rows.
- [x] Invalid `providers` returns a stable `422` code, not a Torrentio error.
- [x] Existing `sort`, `limit` and `quality_filter` behaviour remains backward compatible.

## Test plan

- `tests/torrents/test_filters.py`: min-quality rank, unknown exclusion, dedupe, counts.
- `tests/torrents/test_torrentio_url.py`: providers prefix, empty/whitespace, cache key
  separation.
- `tests/routers/test_releases.py`: new params, 422, limit ceiling, options endpoint.
- `web/src/pages/__tests__/releases.test.tsx`: 2160p visible with the filter, show more,
  localStorage persistence, reset, empty-state copy.

## Implementation notes

- Reuse `QUALITY_RANK`; do not add a second quality table.
- Filtering happens after parsing, so the raw payload stays cacheable and switching filters
  never refetches unless the provider set changes.
- `limit` is a display concern. Do not clamp it so low that a user cannot reach 2160p; the
  point of this ticket is that the server already has the data.
