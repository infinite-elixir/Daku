# T-059 — Torrentio queries every provider and stops discarding 4K rows

**Phase:** 16 · **Depends on:** T-026, T-053 · **Estimate:** S

## Goal

Close the gap between a Stremio search and a Daku search on the same Torrentio addon.
Today Daku always sends a `providers=` subset and a `qualityfilter=` term, so Torrentio
answers with a fraction of what the addon can return. Daku then drops the rows whose
`behaviorHints.filename` is missing and locally filters every `unknown` label.

This ticket makes the shipped defaults match the addon defaults — **all providers, no
upstream quality filter** — keeps the local filter authoritative, and stops treating an
unlabelled 4K/remux row as junk.

No new source is introduced here. T-060 and T-061 build on the result.

## Scope

### Torrentio config defaults (`src/daku/config.py`)

- `TORRENTIO_DEFAULT` becomes `"sort=seeders"`: no `providers=` part and no
  `qualityfilter=` part. Absent `providers=` means *every* Torrentio provider
  (`yts, eztv, rarbg, 1337x, ext, thepiratebay, kickasstorrents, torrentgalaxy, magnetdl`).
- `DEFAULT_PROVIDERS` stays, but its role changes: it is the **known provider list offered
  in the UI**, not a default selection. Rename the docstring/comment accordingly; do not
  rename the constant (it is imported by `routers/releases.py` and the SPA options payload).
- `Settings.providers` defaults to `[]` and means "all providers". Document this in
  `AGENTS.md` §4 next to `torrentio_config`/`providers`.

### Settings resolution (`repositories/settings.py`, `routers/settings.py`)

- `default_settings_map` seeds `providers_movie` / `providers_series` / `providers_anime`
  as `[]`.
- `effective_release_defaults` already falls back to the config prefix; keep that, but `[]`
  must survive as `[]` (no silent re-seeding from `DEFAULT_PROVIDERS`).
- `PATCH /api/settings` accepts `providers: {"movie": []}` and stores an empty list. The
  current "1–20 providers" rule becomes "0–20 providers"; an empty list renders as **All
  providers** in the UI. Invalid names are still rejected with `PROVIDER_RE`.
- `GET /api/settings` keeps returning `config_providers` (the known list) and the stored
  `providers` value so the SPA can distinguish "all" from an explicit set.

### Migration 15 (`src/daku/db.py`)

Append `_migration_015` — never edit a shipped migration. It normalises installs that never
touched the old defaults:

- If a `settings` row `torrentio_config` decodes to exactly the old value
  `providers=yts,eztv,1337x,thepiratebay,torrentgalaxy|qualityfilter=scr,cam,unknown|sort=seeders`,
  rewrite it to `sort=seeders`.
- If a `settings` row `providers_movie` / `providers_series` / `providers_anime` decodes to
  exactly `["yts","eztv","1337x","thepiratebay","torrentgalaxy"]`, rewrite it to `[]`.
- Any other stored value is user intent and is left untouched.

### Router (`routers/releases.py`)

- `configured_providers` must treat `[]` as "send no `providers=` part" (already true through
  `with_providers(config, [])`), and must not fall back to `DEFAULT_PROVIDERS`.
- `/api/releases/options` keeps returning the union of `DEFAULT_PROVIDERS` and the stored
  value as the selectable list, plus a `defaults.providers` of `[]` meaning all.

### Filename fallback (`services/torrents/torrentio.py`)

- `parse_stream` currently drops any stream without `behaviorHints.filename`. Replace the
  drop with a fallback chain:
  1. `behaviorHints.filename`, sanitised;
  2. the stream `title` text before the first info separator (the part before the line that
     carries `👤`/`💾`/`⚙`), sanitised;
  3. `f"release-{info_hash[:12]}"`.
- Rows with no usable info hash or no buildable magnet are still dropped. Never log the raw
  title or magnet.

### Local quality filter (`services/torrents/ranking.py`, `routers/releases.py`)

- Add `is_high_quality_unlabelled(release) -> bool`: `release.quality == unknown` **and** the
  filename matches `\bremux\b` (case-insensitive). This is the only label the parser can
  miss while the row is unambiguously 4K-grade; `2160p`/`4k`/`uhd` already map to `2160p`.
- Move the router's inline blocked-label comprehension into `filter_quality`, extending it
  with: keep a row when `release.is_pack` (existing behaviour) **or**
  `is_high_quality_unlabelled(release)`, even if `unknown` is blocked.
- `apply_min_quality` is unchanged: an explicit `min_quality=2160p` still excludes unknown.

## Out of scope

New sources (T-060/T-061), per-provider enable/disable beyond the existing multi-select,
Torrentio `sizefilter`/`limit`/`language` options, and any change to sort modes.

## Acceptance criteria

- [x] The shipped config produces `https://torrentio.strem.fun/sort=seeders/stream/movie/tt….json`
      with no `providers=` and no `qualityfilter=` part.
- [x] A stored empty `providers` sends no `providers=` part; an explicit list still rewrites it.
- [x] Migration 15 rewrites only unchanged shipped defaults and leaves customised values alone.
- [x] `PATCH /api/settings` accepts an empty per-kind provider list and the UI shows "All providers".
- [x] A stream without `behaviorHints.filename` is kept with a title-derived or hash-derived filename.
- [x] `quality_filter=unknown` keeps an unknown-quality row whose name contains `remux`, and still
      drops other unknown rows.
- [x] `/api/releases/options` still lists every known provider and reports `defaults.providers=[]`.
- [x] Existing tests for URL building, parsing, filtering and settings pass with updated expectations.
- [x] `AGENTS.md` §4 and §6 describe `providers=` empty as "all providers".

## Test plan

- `tests/torrents/test_torrentio_url.py`: default prefix has no `providers`/`qualityfilter`;
  explicit list rewrites; empty list leaves the prefix alone.
- `tests/torrents/test_torrentio_parse.py`: stream without `behaviorHints.filename` parses from
  the title line; stream with neither falls back to the hash-derived name.
- `tests/torrents/test_filters.py`: `is_high_quality_unlabelled`, pack exemption and remux
  exemption under `quality_filter=["unknown"]`.
- `tests/test_db_migrations.py` (or the existing migration test module): migration 15 rewrites
  the untouched defaults and preserves a customised provider list.
- `tests/routers/test_settings.py`: empty provider list round-trip and validation.
- `tests/routers/test_releases.py`: default request sends no `providers=` part.

## Notes

Implemented. Migration 15 rewrites only the untouched shipped defaults; the local filter keeps
packs and unlabelled remuxes. Tests cover URL building, parsing fallbacks, filtering, migration
and settings round-trips.
