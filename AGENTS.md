# AGENTS.md — Daku

Daku is a **single-user, LAN-only, self-hosted media library manager** for Linux.
It searches metadata (IMDb/Cinemeta, AniList), finds releases on Torrentio and nyaa.si (anime),
downloads them with qBittorrent (torrents) or aria2c (direct URLs), files them into a Jellyfin
library, and mirrors library entries into a Telegram archive channel via a bot.

Daku **does not play video itself**. Jellyfin owns playback, remuxing and transcoding.

---

## 1. Hard constraints

These are decisions, not preferences. Do not re-litigate them in a ticket.

| Area | Decision |
|---|---|
| Deployment | One background service + responsive web UI on `0.0.0.0:8420` |
| Host | Linux with systemd, XDG paths. Windows/macOS are later ports, not v1. |
| Auth | None. LAN only. Never add internet-facing auth without a new decision record |
| Playback | Deep-link to Jellyfin. No in-app transcode, no HLS packaging, no `<video>` for non-MP4 |
| Torrent engine | qBittorrent via Web API v2. **Never** use aria2c as the torrent client |
| Torrent metadata | libtorrent may inspect a magnet read-only (metadata only, no payload, no download row) to list files. qBittorrent remains the only download engine |
| Magnet probe | Optional `daku[probe]` extra. One lazy, serialised session; no listener; DHT only; probe dirs under `data_dir/probes` are always deleted. Never a download engine |
| Direct downloads | aria2c via JSON-RPC (Telegram fallback, HTTP/DDL) |
| Download model | Download fully, then organise. No sequential/stream-while-downloading in v1 |
| Library source | Only artefacts Daku created. No scanning of pre-existing folders |
| Anime | AniList/Jikan metadata + `anibridge-mappings` for IDs and episode ranges |
| Telegram | Bot API only (no MTProto user account). Archive channel is write-only from Daku's side |
| Frontend | React + TypeScript + Vite, served as static files by the same process |

**Non-goals for v1:** multi-user, public internet exposure, subtitles search, seeding
policy UI, *arr integration, Plex/Emby, mobile native app, in-app player.

**Legal:** Daku indexes public metadata and hands magnets to a torrent client. It hosts
no content. Keep the disclaimer in the README and settings page.

---

## 2. Architecture

```
Browser / PWA ──HTTP+SSE──> FastAPI (daku) ──> SQLite (daku.db)
                                  │
                                  ├─ Metadata: IMDb suggestions → Cinemeta → AniList/Jikan
                                  ├─ Releases: Torrentio adapter
                                  ├─ Engines: qBittorrent Web API | aria2c JSON-RPC
                                  ├─ Organiser: ffprobe + hardlink/copy → Jellyfin dirs
                                  ├─ Jellyfin: POST /Library/Refresh, GET /Items
                                  └─ Telegram: bot (python-telegram-bot) → local Bot API server
```

Processes on the host:

1. `daku` — API, UI, worker loop, run as a `systemd --user` unit.
2. `qbittorrent-nox` — user-installed, Web UI enabled on `127.0.0.1:8080`.
3. `aria2c` — spawned by Daku with `--enable-rpc` on `127.0.0.1:6800`.
4. `telegram-bot-api` — **optional but required for files > 20 MB**, `--local`, port `8081`.
5. `jellyfin` — user-installed, port `8096`.

### Repository layout

```
src/daku/
  __main__.py          # entrypoint, uvicorn
  app.py               # FastAPI factory, router mounting, static SPA
  config.py            # paths, Settings, env/JSON config
  db.py                # engine, session, user_version migrator
  models.py            # SQLAlchemy models
  events.py            # in-process pub/sub → SSE
  worker.py            # poll loop, state machine transitions
  routers/             # search, titles, releases, downloads, telegram, jellyfin, system
  services/
    metadata/          # imdb.py, cinemeta.py, anilist.py, jikan.py, mapping.py
    torrents/          # base.py, torrentio.py
    engines/           # base.py, qbittorrent.py, aria2.py
    organize/          # ffprobe.py, naming.py, organizer.py
    telegram/          # bot.py, attach.py, archive.py
    jellyfin.py
    doctor.py
web/                   # React SPA (Vite)
tests/                 # pytest + respx
tickets/               # implementation tickets (read the assigned one first)
```

---

## 3. Commands

```bash
uv sync                                  # backend deps
uv sync --extra probe                    # optional libtorrent magnet inspection
uv run daku                              # serve on 0.0.0.0:8420
uv run daku doctor                       # probe every integration without serving (--json, --strict)
scripts/check.sh                         # ruff + format + mypy + pytest: the one check to run
scripts/check.sh --web                   # same, plus pnpm -C web test
pnpm -C web install
pnpm -C web dev                          # Vite on :5173, proxies /api to :8420
pnpm -C web build                        # emits into src/daku/webdist/
pnpm -C web test

# Linux install (unit file: packaging/daku.service)
systemctl --user status daku
journalctl --user -u daku -f
```

Do not add a command to this list that does not actually work on a clean checkout.

---

## 4. Configuration

Resolution order: environment → `~/.config/daku/config.json` → defaults.

| Key | Default | Notes |
|---|---|---|
| `host` | `0.0.0.0` | LAN binding is intentional |
| `port` | `8420` | Avoid 8080/8081/8096/6800 |
| `config_dir` | `~/.config/daku` | config + `secrets.json` (0600) |
| `data_dir` | `~/.local/share/daku` | DB, logs, mappings |
| `downloads_dir` | `~/Downloads/Daku` | engine save path |
| `movies_dir` | `~/Media/Movies` | must be a Jellyfin library |
| `tv_dir` | `~/Media/TV` | must be a Jellyfin library |
| `anime_dir` | `~/Media/Anime` | must be a Jellyfin library |
| `organize_mode` | `hardlink` | `hardlink` → `copy` fallback → `move` |
| `magnet_probe` | `true` | optional libtorrent metadata probe; `false` reports `disabled` |
| `qbt_url` / `qbt_user` / `qbt_pass` | `http://127.0.0.1:8080` | password in secrets |
| `aria2_rpc` / `aria2_secret` | `http://127.0.0.1:6800/jsonrpc` | |
| `jellyfin_url` / `jellyfin_api_key` | `http://127.0.0.1:8096` | key in secrets |
| `omdb_api_key` | empty | optional; enables Rotten Tomatoes/Metacritic ratings (secrets) |
| `telegram_bot_token` | empty | in secrets |
| `telegram_api_base` | `https://api.telegram.org` | set to local server to lift the 20 MB cap |
| `telegram_archive_channel` | empty | numeric `-100...` id |
| `telegram_allowlist` | `[]` | numeric user ids; empty = deny all |
| `torrentio_config` | `providers=yts,eztv,1337x,thepiratebay,torrentgalaxy|qualityfilter=scr,cam,unknown|sort=seeders` | override of last resort |
| `providers` | `yts,eztv,1337x,thepiratebay,torrentgalaxy` | default Torrentio provider set; comma string or list |
| `quality_filter` | `cam,scr,unknown` | labels dropped after parsing; empty means no local filter |
| `sort` / `limit` | `seeders` / `20` | release display defaults; API limit ceiling is 200 |
| `providers_movie` / `providers_series` / `providers_anime` | current `providers` | persisted per-kind release provider defaults in SQLite |
| `default_min_quality` | empty | persisted release minimum quality; empty means any |
| `default_sort` / `default_limit` | current `sort` / `limit` | persisted release display defaults |

`secrets.json` is written with owner-only permissions. Never log it, never commit it.

---

## 5. Data model invariants

- SQLite, WAL mode, foreign keys ON, `busy_timeout=5000`.
- All IDs are UUIDv4 strings generated in Python. No autoincrement PKs except `events`.
- Timestamps are UTC ISO-8601 strings. UI converts.
- Schema changes are **append-only migrations** in `db.py`, gated by `PRAGMA user_version`.
  Never edit a shipped migration; add the next one.
- `titles` is the library. It may contain entries with **no downloads and no files**; that is a
  valid state, not an error. `episodes` exists only for `series` and `anime`.
- Library membership is independent of availability. `titles.wanted` is user intent;
  title/episode availability is derived from `media_files` and is never set by hand.
- `releases` are saved independently of `downloads`. Saving a magnet creates or updates a
  release row with `is_saved = 1` and resolved metadata, and **must not** create a download.
  Deleting a download never deletes the saved release.
- One `media_files` row per physical file. A download may yield several (samples, extras);
  exactly one may have `is_primary = 1`.
- Removing a download row never deletes a library file and never cascades `media_files`.
  The API detaches media rows (`download_id = NULL`) before deleting the queue entry, so
  `lib_path` and title/episode availability survive. `delete_files` only reaches the
  engine's download directory.
- `downloads.engine` is `qbittorrent`, `aria2`, or `local` (same-host Telegram file copy).
  `downloads.external_id` is the qBittorrent info-hash, the aria2 GID, or the Telegram
  `message_id` respectively. `UNIQUE(engine, external_id)` prevents double-tracking.
  Because of that key, a finished season pack keeps **one** download row: asking for another
  episode reuses the row, replaces the selection and lets the worker re-apply file priorities
  on the adopted torrent. The row keeps every `media_files` file it has produced and exposes
  them as `downloaded_files`; re-inspection replaces only the files being inspected. A request
  is a duplicate only when the requested file/episode is already in `media_files` (T-042).
- `telegram_messages` is keyed by `(channel_id, message_id)`; `file_unique_id` is the dedupe key.
- T-005 adds `anime_mappings` and `anime_mapping_overrides` (migration v2).
  T-006 adds `titles.folder_name` and the partial unique indexes on `imdb_id`/`anilist_id`
  (migration v3). T-014 adds `jellyfin_item_id` to `titles` and `media_files` (migration v4).
  Migrations are still append-only.
- T-029 adds `title_overrides` (one row per editable identity field the user set),
  `title_episode_overrides` (per-episode renumbering) and `title_season_offsets`
  (provider season → library season), migration v7. `titles.*` is still updated so readers
  keep working; the override rows stop a metadata refresh from overwriting the decision.
  Deleting a title cascades all three. No file on disk is deleted by any fixer operation.
- T-031 adds `downloads.selected_files_json` (JSON array of engine file indices; `null`
  means the engine default/all) and `downloads.file_match_json` (matcher result plus the
  selected paths and, when a selection is still pending, `selection_pending`), migration v9.
  These are metadata only: they never introduce a download state.
- Deleting a title cascades to episodes/releases/files but **never** deletes files on disk
  unless the caller passes `keep_files=false` explicitly.

---

## 6. Integration contracts (verified 2026-10)

### IMDb suggestions
`GET https://v2.sg.media-imdb.com/suggestion/{first}/{query}.json`
Normalise: lowercase, spaces → `_`, strip non `[a-z0-9_.-]`. Keep only `id` matching `^tt\d+$`.
Map `qid`/`type` → `movie` | `series`.

### Cinemeta
- Search: `GET https://v3-cinemeta.strem.io/catalog/{movie|series}/top/search={urlencoded}.json`
- Meta: `GET https://v3-cinemeta.strem.io/meta/{type}/{ttid}.json`
Use as fallback when IMDb suggestions return nothing or time out. Episodes come from
`meta.videos[]` as `tt...:season:episode`.

### Torrentio
`GET https://torrentio.strem.fun/{config}/stream/{movie|series}/{id}.json`
`id` is `tt1234567` or `tt1234567:2:5`. Parse each stream:
- seeders from `👤 N` in `title`
- size from `💾 X` in `title`
- provider from `⚙ X` in `title`
- filename from `behaviorHints.filename`
- build the magnet locally from `infoHash` + `&tr=` every `sources[]` entry starting with `tracker:`
Cache responses for 1 h; on failure serve stale cache and mark the response `stale=true`.
Never assume a field exists.

Discovery controls (`GET /api/titles/{id}/releases`): `providers`, `min_quality`, `sort`,
`limit` (max 200). The order is parse → dedupe by lowercased `info_hash` (keep highest
seeders) → drop `quality_filter` labels → `min_quality` (unknown excluded) → sort → slice.
The raw-payload cache key is the URL, so a different provider set is a different entry.
`providers=` is rewritten with `with_providers`; empty means the configured default.
`GET /api/releases/options` exposes the provider/label/sort choices and limit steps.

### Nyaa RSS
Anime-only release discovery may also use `https://nyaa.si/?page=rss&q=...&c=1_2&f=0&s=seeders&o=desc`.
The query is URL-encoded, control characters are removed, and it is capped at 120 characters.
Daku sends a descriptive `User-Agent`, throttles requests to one per second per process, parses
only RSS items, and builds magnets locally from a verified 40-hex or 32-base32 info hash.
The torrent link is never downloaded. Default category `1_2` is Anime — English-translated;
`1_1` and `1_3` are also supported. Raw RSS is cached for one hour by full URL, and stale
payloads are served on upstream failure. Nyaa is never queried for movies or non-anime TV.

### Magnet probe (optional libtorrent)
- `POST /api/magnets/inspect` `{magnet}` and `GET /api/releases/{id}/files` return torrent
  metadata only: name, total size, piece length, private flag, file list. Both are read-only.
- One lazy libtorrent session per process, in a worker thread,
  `listen_interfaces=0.0.0.0:0` with `enable_incoming_tcp=False` and
  `enable_incoming_utp=False`; DHT on, LSD/UPnP/NAT-PMP off. DHT needs a non-loopback UDP
  socket: binding `127.0.0.1:0` or `""` leaves `dht_nodes=0` and every probe times out.
  Incoming peer connections stay disabled, so the probe only fetches metadata and never
  serves or receives payload. Add the magnet in `upload_mode` under
  `data_dir/probes/<info_hash>` and remove it after success, timeout or error.
- One active probe, at most 10 outstanding, 30 s deadline. Reject the 11th with
  `429 PROBE_BUSY`. Success cache 24 h / LRU 200; failure cache 5 min. A per-hash lock
  deduplicates clicks.
- Errors: `400 INVALID_MAGNET`, `503 PROBE_UNAVAILABLE` (extra not installed),
  `504 PROBE_TIMEOUT`, `429 PROBE_BUSY`, `502 PROBE_FAILED` (payload guard or adapter error).
- Log `info_hash[:12]` only. Never log a magnet, tracker or probe path. No probe creates a
  download row or touches qBittorrent. The doctor reports `ok|unavailable|disabled` without
  starting a probe.

### qBittorrent Web API v2
- `POST /api/v2/auth/login` (form: `username`, `password`) → `SID` cookie.
  A successful login is `200 Ok.` on 4.x/5.0–5.1 and an empty `204` on 5.2+; a wrong
  password is `401` and a rate-limit ban is `403` with a "banned" body. Accept both
  success shapes — never require `200 Ok.` alone.
- `POST /api/v2/torrents/add` (multipart): `urls`, `savepath`, `category=daku`,
  `tags=<title_id>`, `sequentialDownload=false`, `firstLastPiecePrio=true`, and both
  `paused=<bool>` (4.x/5.0/5.1) and `stopped=<bool>` (5.2+) with the same value.
  A successful add may answer `200 Ok.`, an empty `2xx`, or (5.2+) JSON
  `{success_count, failure_count, pending_count, added_torrent_ids}`. Accept the JSON as
  success when `failure_count == 0` and `success_count >= 1` or `pending_count >= 1`
  (a `202` with pending metadata is success). Never put the raw body in `download.error`.
- `GET /api/v2/torrents/info?hashes=<hash>` → `progress`, `dlspeed`, `eta`, `state`, `size`.
- `GET /api/v2/torrents/files?hash=<hash>` → file list with `progress` and `priority`.
- Removing and deleting data are separate calls (`deleteFiles=true`).
- Web API `2.11.0` (qBittorrent 5.0) renamed pause/resume to stop/start. Daku probes
  `GET /api/v2/app/webapiVersion` once per process: `>= 2.11.0` uses
  `/torrents/stop|start`, older uses `/torrents/pause|resume`. If the probe fails, it tries
  start/stop first and falls back on `404`, caching the working pair.
- File selection is enforced with the **stop → `filePrio` → verify → start** contract.
  A stopped magnet has no metadata, so start it briefly, stop as soon as
  `/torrents/files` returns the list, then POST one numeric `priority` per call with the
  matching pipe-separated file `id` list (selected `1`, others `0`). Read
  `/torrents/files` back until every priority matches, and only then start. A stop failure
  is retried and surfaced as `QBT_FILE_SELECTION_PENDING`, so the worker backs off and
  stops the torrent again. Add-time `filePriorities` are **not** used: Daku adds
  magnets by URL, and qBittorrent rejects add-time priorities until it has fetched the
  metadata itself. That never replaces the stop/verify path. Live re-selection uses the
  same sequence and restores the previous priorities (stopped) when the new selection is
  rejected.
- Linux uses `qbittorrent-nox`; its Web API is identical to the GUI build.

### aria2 JSON-RPC
`POST http://127.0.0.1:6800/jsonrpc`, method `aria2.addUri`, params `[token, [url], options]`.
Options: `dir`, `out`, `continue=true`, `max-connection-per-server=8`.
Poll with `aria2.tellStatus(gid, ["status","completedLength","totalLength","downloadSpeed"])`.
Terminal states: `complete`, `error`, `removed`.

### Jellyfin
- Auth header: `Authorization: MediaBrowser Token=<api_key>`.
- Scan: `POST /Library/Refresh` (admin API key required).
- Items: `GET /Items?Recursive=true&IncludeItemTypes=Movie,Series&Fields=Path,ProviderIds`.
- Direct play URL for the UI: `/Videos/{itemId}/stream?static=true&api_key=<key>`.
- Naming (from Jellyfin docs, follow exactly):
  - Movie: `Movies/Name (Year) [imdbid-tt0000000]/Name (Year) [imdbid-tt0000000].mkv`
  - Series: `TV/Name (Year) [imdbid-tt0000000]/Season 01/Name (Year) - S01E02 - Ep Title.mkv`
  - Season folder is `Season 01`, never `S01`. Specials go in `Season 00`.
  - Anime is filed as TV using **the provider's** season/episode. Absolute numbers are
    stored in the DB for display only.

### Telegram Bot API
- Cloud API: `getFile` download cap is **20 MB**.
- Local server (`--local --http-port=8081`) removes the download cap and returns an
  **absolute local path** in `file_path`; `getFile` can then be a filesystem copy.
- Upload cap on a local server is 2000 MB. Per-file Telegram limit is 2 GB (4 GB premium).
- Switching to a local server requires calling `logOut` on the cloud API once:
  `GET https://api.telegram.org/bot<token>/logOut`.
- Bots **cannot read channel history**. Daku only knows messages it itself copied to the
  archive channel. Never design a feature that requires backfilling old channel posts.
- `python-telegram-bot` accepts `base_url=` for the local server.

---

## 7. State machine (do not invent parallel states)

```
download: queued → active → complete → organizing → available
                ↘ paused ↗          ↘ failed → (retry) queued
                ↘ cancelled
episode:  missing → queued → downloading → available
title:    none → partial → complete
```

The worker is the only writer of these transitions. Routers enqueue; they do not mutate
progress. Every transition emits an event to SSE.

Manual metadata changes (T-029) are not a state-machine state: identity and episode
overrides are user decisions persisted in the `title_*_override(s)` tables and applied by
the refresh path. They never add a new download or episode status.

File selection (T-031) is also not a new state. `downloads.selected_files_json` and
`downloads.file_match_json` say which torrent files the existing `active` download may
fetch. A metadata timeout is a transient engine error (`FILE_SELECTION_PENDING`) while the
torrent stays `paused`; the worker retries the selection with backoff. Nothing resumes the
whole pack and the organiser only sees the selected files. The magnet necessarily adds the
whole torrent: "download only one file" means file priorities, never a partial torrent.
Selection is metadata, not a state; a failed selection is an ordinary `failed` row.

---

## 8. Code style

Python: 3.12, full type hints, `from __future__ import annotations`, Pydantic v2 models at
the boundary, plain dataclasses internally. No `requests`. No blocking I/O in async paths —
wrap `ffprobe`/`os.link` in `asyncio.to_thread`.

TypeScript: strict mode, no `any` in exported signatures, TanStack Query for server state,
no global store unless a ticket requires it. Components in `PascalCase.tsx`.

Errors: user-facing messages go through `daku.errors.AppError` with a stable `code`.
Engine and network failures are retried with jittered backoff, then surfaced in `/api/system/doctor`.

Logging: stdlib `logging`, one JSON line per event, never log magnets, tokens or passwords.

---

## 9. Ticket workflow

1. Read the assigned ticket in `tickets/` plus `plan.md`. Do not start a later ticket early.
2. Implement only the ticket's scope. If the scope is wrong, update the ticket and say so.
3. Add tests for the acceptance criteria. Network calls must be mocked with `respx`.
4. Run `uv run pytest -q`, `uv run ruff check .`, and `pnpm -C web test` if the UI changed.
5. Tick the acceptance checkboxes in the ticket file and note anything deferred.

Definition of done: acceptance criteria checked, tests green, no new TODO without a ticket,
`/api/system/doctor` still reports healthy for the touched integration.

---

## 10. Gotchas

- Always use `pathlib.Path`. Never string-concatenate paths. Linux is case-sensitive and
  treats `\` as a normal character, so a Windows-style name will "work" here and break a port.
- Hardlinks fail across filesystems. Detect with `st_dev`; fall back to copy, then move.
- qBittorrent may report `progress=1` before the file is flushed. Verify the file exists
  and its size is stable for 5 s before organising.
- Torrent filenames can contain `/`, NUL, control chars, emoji and RTL marks. Sanitise for a
  portable library: strip `/` and NUL, and also strip Windows-reserved names so a later port
  does not break.
- A "series" Torrentio id without `:season:episode` returns the whole-pack results. Only do
  that when the user explicitly asks for a season pack.
- Jellyfin may need a second scan before an item appears. Poll `/Items` for the created path.
- The Telegram bot token must never appear in a URL written to logs.
- `--local` bot API changes `file_path` semantics. Test both modes.
