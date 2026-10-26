# T-008 — Saved magnets, manual magnets, download later

**Phase:** 2 · **Depends on:** T-006, T-007 · **Estimate:** M

## Goal

Let the library hold entries and releases **without downloading anything**. A user can save a
Torrentio result — or paste their own magnet — attach it to a title or episode, and download it
days later. This is the "save, then decide" half of the product.

Saving must work with **no download engine configured**. T-009 is only needed when the user
actually presses Download.

## Scope

- `repositories/releases.py`: upsert by `(info_hash, episode_id)`, save/unsave, list saved,
  refresh metadata, mark dead.
- `services/releases/magnet.py`: parse a manual magnet URI — `xt=urn:btih:` (hex or base32),
  `dn`, every `tr`; normalise to the same `Release` shape as Torrentio.
- API:
  - `POST /api/releases/{id}/save` → `is_saved=1`, `saved_at=now`
  - `DELETE /api/releases/{id}/save`
  - `POST /api/releases` `{magnet, title_id, episode_id?}` → resolve, persist, save
  - `GET /api/titles/{id}/releases/saved?episode_id=`
  - `POST /api/releases/{id}/refresh` → re-check seeders by `info_hash`
  - `POST /api/downloads {release_id}` is the only path that leaves the saved state
- `metadata_json` snapshot written at save time: `source`, `video_codec`, `audio_hint`,
  `languages`, `release_group`, `edition`, plus `resolved_from` (`torrentio` | `manual`).
- UI: **Saved** tab on the title page; star toggle in the release list; "Download" button;
  `last_checked_at` + seeders; a `dead` badge with a "find another" link.
- Every release the user selects is kept as a **recent** row. `is_saved` marks **explicit**
  intent to keep it. Recent is implicit (row exists, `is_saved=0`), saved is explicit
  (`is_saved=1`); both live on the same row, and only saved rows appear in the Saved tab.

## Out of scope

Auto-download when a saved magnet reappears, RSS, bulk import, cross-title recommendations,
background seeder polling for unsaved releases.

## Acceptance criteria

- [x] Saving a release creates or updates one row with `is_saved=1`, `saved_at`, and
      `metadata_json`, and creates **zero** `downloads` rows and zero files on disk.
- [x] A saved release survives a process restart and is returned by the Saved tab.
- [x] `POST /api/releases` with a valid magnet stores `info_hash` (lowercase hex), `filename`
      from `dn`, and all trackers; `source="manual"`.
- [x] A magnet with a base32 `btih` is converted to the correct 40-char hex hash.
- [x] A magnet with no `btih`, a malformed hash, or a non-`magnet:` URI returns `400` with
      `INVALID_MAGNET`.
- [x] The same `info_hash` saved twice for the same episode updates metadata and keeps
      `is_saved=1`; it never creates a second row.
- [x] `POST /api/downloads {release_id}` works for a release saved before a restart, and the
      download row links back to it.
- [x] Unsaving removes it from the Saved tab but does not affect an in-flight or completed
      download.
- [x] `POST /api/releases/{id}/refresh` updates `seeders` and `last_checked_at`; on fetch
      failure it keeps the previous seeders and does not set `0`.
- [x] After 3 consecutive refresh misses the release is marked `dead=1` but is **not** deleted.
- [x] Saved and unsaved releases can both be listed; the Saved tab is filterable by quality
      and by `dead`.
- [x] Opening the Saved tab triggers at most one refresh per release per 10 minutes.
- [x] `GET /api/titles/{id}` includes `saved_release_count` so the library grid can show
      "2 saved" on an entry with no files.

## Implementation notes

Upsert must preserve identity and intent:

```sql
INSERT INTO releases (...) VALUES (...)
ON CONFLICT(info_hash, episode_id) DO UPDATE SET
  seeders = excluded.seeders,
  size_bytes = excluded.size_bytes,
  filename = excluded.filename,
  metadata_json = excluded.metadata_json,
  last_checked_at = excluded.last_checked_at,
  -- never reset is_saved or saved_at here
```

`episode_id` is nullable: a movie or an unspecified episode saves against the title.

Magnet parsing: `urllib.parse.urlsplit`, then `parse_qs`; `xt` values starting `urn:btih:`
are the hash; 32 chars → base32 decode to 20 bytes → `.hex()`; 40 chars → lowercase hex.
Percent-decode `dn`. Reject hashes that are not 20 bytes after conversion.

`metadata_json` is a snapshot. Do not recompute hints from the filename on read — the filename
may be renamed later by the organiser.

Refresh is best-effort: fetch the Torrentio stream list for the title/episode, find the entry
with the same `info_hash`, copy `seeders`/`size_bytes`, update `last_checked_at`. Cache the
Torrentio payload so refreshing 20 saved releases is one upstream request.

Dead logic lives in the repository, not the router: `miss_count` increments on a successful
fetch that does not contain the hash; reset to 0 when it reappears. Only a successful fetch may
increment or reset.

Saving is allowed while a download exists; that is how a user keeps a release around after
completing it.

## Test plan

- `tests/releases/test_magnet.py`: hex, base32, `dn` with spaces/Unicode, multiple `tr`,
  missing `tr`, invalid hash, non-magnet URI.
- `tests/releases/test_save.py`: save creates no download, idempotent upsert, unsave keeps
  download, restart persistence.
- `tests/releases/test_refresh.py`: seeders updated, fetch failure preserves old value,
  miss_count → dead at 3, reappearance resets.
- `tests/routers/test_saved_releases.py`: list shape parity with T-007, filters, count on title.
- `tests/releases/test_download_later.py`: save → restart → `POST /api/downloads` → linked row.

## Implemented 2026-10-23

- `services/releases/magnet.py` parses `xt=urn:btih:` (40 hex or 32 base32 → 20 bytes →
  lowercase hex), percent-decodes `dn`, keeps every `tr` in order and deduplicates it, then
  rebuilds a canonical magnet. Invalid input raises `MagnetError` → `400 INVALID_MAGNET`.
- `services/releases/metadata.py` owns the durable `metadata_json` snapshot
  (`source`, `video_codec`, `audio_hint`, `languages`, `release_group`, `edition`,
  `resolved_from`). Snapshots are written at save time and never recomputed on read.
- `repositories/releases.py` gained `find_by_hash`, a real
  `INSERT … ON CONFLICT (info_hash, COALESCE(episode_id, '')) DO UPDATE` upsert that fills
  metadata but never resets `is_saved`/`saved_at`, plus `record_seen`/`record_miss` and
  `to_payload`. `miss_count` only moves on a successful upstream fetch.
- `services/releases/refresh.py` batches saved releases into one Torrentio request per
  `(title, episode)` and throttles attempts to one per release per 10 minutes. A manual
  magnet that Torrentio does not index is not counted as dead: absence is not proof.
  Network failures leave seeders and `miss_count` untouched.
- API: `POST /api/releases` (defaults to saving; `save:false` records a *recent* row for a
  direct Download), `POST|DELETE /api/releases/{id}/save`,
  `GET /api/titles/{id}/releases/saved?episode_id=&quality=&dead=`,
  `POST /api/releases/{id}/refresh`, and `POST /api/downloads {release_id}` (201 new, 200
  deduplicated by info-hash). Search results now carry `release_id`, `is_saved`, `dead`,
  `saved_at`, `last_checked_at` so the star can toggle without a second lookup.
- `services/queue.py` + `routers/downloads.py` create the queued download row and link
  `downloads.release_id`. T-009 will attach the qBittorrent engine to that same row;
  `GET /api/downloads` already returns title/episode context.
- UI: a **Releases** section on the title page with Find/Saved tabs, season/episode picker,
  star toggles, Download, manual magnet form, quality + hide-dead filters, seeders,
  `last_checked_at`, a Dead badge and "Find another". The library grid shows "N saved".
- Tests: `tests/releases/test_magnet.py`, `test_save.py`, `test_saved_refresh.py`,
  `test_download_later.py`, `tests/routers/test_saved_releases.py`, and
  `web/src/pages/__tests__/releases.test.tsx`.
- Notes: the acceptance line `source="manual"` is represented by `provider="manual"` and
  `metadata_json.resolved_from="manual"`; `metadata_json.source` remains the video source
  (BluRay/WEB-DL/...). "Saved and unsaved can both be listed" is covered by the search
  listing (annotated saved state) plus the Saved tab; no separate recent-only endpoint.
  Deferred by design: background polling for unsaved rows, RSS, bulk import.
