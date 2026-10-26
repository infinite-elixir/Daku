# T-007 — Torrentio: search, parse, rank

**Phase:** 2 · **Depends on:** T-003 · **Estimate:** M

## Goal

Given a title or an episode, return a ranked, human-readable list of releases with a
ready-to-use magnet. This is a direct port of the proven logic in `../Tsearch/tsearch`.

## Scope

- `services/torrents/base.py`: `ReleaseProvider` protocol (`search(type, id) -> list[Release]`).
- `services/torrents/torrentio.py`: URL builder with config prefix, fetch, parse, magnet build.
- `services/torrents/ranking.py`: quality detection, byte parsing, sort modes.
- `services/torrents/cache.py`: 1 h TTL + stale fallback.
- `routers/releases.py`: `GET /api/titles/{id}/releases?season=&episode=&sort=&limit=`.
- Config: `torrentio_config`, `sort`, `limit`, `quality_filter`.

## Out of scope

Saving a release (T-008), sending to a client (T-009), providers other than Torrentio.

## Acceptance criteria

- [x] Movie query produces `/stream/movie/tt1254207.json`; episode query produces
      `/stream/series/tt0944947:2:5.json`.
- [x] The config prefix is only added when non-empty and is correctly `|`-joined.
- [x] Every returned release has `info_hash`, `filename`, `quality`, `size_bytes`,
      `seeders`, `provider`, `magnet` or is dropped.
- [x] Each release exposes resolved hints parsed from the filename/title in `metadata_json`:
      `source` (`BluRay`, `WEB-DL`, `WEBRip`, `HDTV`, `REMUX`, unknown), `video_codec`
      (`x264`, `x265`, `AV1`, unknown), `audio_hint` (`AAC`, `AC3`, `DDP`, `DTS`, `TrueHD`,
      `Atmos`, unknown), `languages`, `release_group`, `edition`. Missing hints are `null`,
      never guessed from the provider name.
- [ ] Quality labels cover `2160p`, `1440p`, `1080p`, `720p`, `576p`, `480p`, `cam`,
      `scr`, `ts`, `unknown`, and are case-insensitive.
- [x] `cam`/`scr` are filtered by default (`quality_filter` setting) and can be re-enabled.
- [x] Sort modes `seeders`, `quality`, `size` work and are stable on ties.
- [x] Magnets include the filename `dn` and every `sources[]` entry starting with `tracker:`.
- [x] A malformed stream entry is skipped, never raises.
- [x] Response includes `stale` and `cached_at`.
- [x] An empty result returns `200` with `releases: []`, not `404`.

## Implementation notes

Build the torrentio prefix from `providers`, `qualityfilter`, `sort` exactly as tsearch does:
`providers=yts,eztv|qualityfilter=scr,cam|sort=seeders`.

Stream title format from Torrentio is multi-line:
```
Torrentio
1080p
Big Buck Bunny (2008) 1080p ...
👤 43 💾 209.37 MB ⚙️ ThePirateBay
```
Parse the `👤`, `💾`, `⚙️` line with tolerant regexes. Missing seeders → `0`; missing size →
`None`; missing provider → `"unknown"`.

Magnet:
```
magnet:?xt=urn:btih:<lowercased infoHash>&dn=<urlencoded filename>&tr=<tracker>&tr=...
```
Skip `dht:` sources. Deduplicate trackers.

Size parsing must handle `KB/MB/GB/TB` and `KiB/MiB/...`, decimal and integer, no unit → bytes.

Ranking: seeders desc, then quality rank desc. Quality rank: 2160=60, 1440=55, 1080=50,
720=40, 576=35, 480=30, ts=4, scr=5, cam=3, unknown=1.

Cache the *raw* Torrentio payload, not the parsed result, so ranking changes don't need a refetch.

## Test plan

- `tests/torrents/test_parse.py`: recorded Big Buck Bunny payload, missing fields, malformed
  stream, non-ASCII titles, multi-line titles.
- `tests/torrents/test_rank.py`: each quality label, each sort mode, tie stability, filter.
- `tests/torrents/test_magnet.py`: tracker inclusion, dedupe, URL encoding, `dht:` skipped.
- `tests/routers/test_releases.py`: movie path, episode path, empty, stale fallback.

## Implemented 2026-10-23

- `services/torrents/` now has `base.py` (`Release` + `ReleaseProvider`), `ranking.py`,
  `cache.py` (`PayloadCache`, raw payload + `cached_at`) and `torrentio.py`.
- `GET /api/titles/{id}/releases` is live. Series searches require `season` + `episode`;
  whole-series/season packs require the explicit `pack=true` flag, per the pack gotcha.
  Anime works when the library row has an `imdb_id`; otherwise it returns
  `RELEASE_SOURCE_UNAVAILABLE` (422).
- Config gains `sort`, `limit`, `quality_filter`; `quality_filter` can be emptied per
  request (`?quality_filter=`) to re-enable cam/scr without editing config.
- `/api/system/doctor` gained a `torrentio` check (configuration only, no network).
- Tests: `tests/torrents/test_torrentio_{parse,rank,magnet}.py`,
  `tests/routers/test_releases.py`, plus the recorded payload fixture.
- Deferred: no provider other than Torrentio (out of scope); saving/downloading untouched.
