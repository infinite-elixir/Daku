# Daku — Build Plan

**Status:** v1 planning
**Date:** 2026-10-23
**Inputs:** `vision.md`, `../Tsearch/tsearch`, scoping decisions (below)

---

## 1. What Daku is

Daku is a **self-hosted, single-user media librarian** that runs as a `systemd --user` service
on your Linux machine and is driven from any browser on the LAN — phone, tablet or desktop.

It does four things:

1. **Find** a title (IMDb + Cinemeta, AniList for anime) and put it in a local library.
2. **Find a release** for a movie or a specific episode via Torrentio, and either save it for
   later or send it straight to a downloader.
3. **Download it** with qBittorrent (torrents) or aria2c (direct URLs), with live progress in the UI.
4. **File it** into a Jellyfin library with correct naming, trigger a scan, and hand playback
   to Jellyfin.

A fifth, optional loop: **Telegram**. Forward a file to the bot, attach it to a library entry,
the bot mirrors it to an archive channel, and Daku can pull it back down later.

### A library entry is not a download

This is a first-class rule, not a UI detail:

- You can **add a title and download nothing**. It stays in the library with no file.
- You can **save one or more magnets against it**, with resolved metadata (quality, size,
  seeders, provider, codecs, language hints), and download any of them later.
- **Saved** and **available** are different states. A title with a saved magnet is *saved*,
  not *missing*. A title with a file is *available*.
- Saving is durable: it survives restarts, Torrentio outages and download completions.
- A manual magnet can be saved too, as long as it can be tied to a title or episode.

The one-sentence differentiator: **search → pick → watch, in three taps from any device on the
LAN, with no *arr stack and no config files.** The zero-tap alternative matters too:
**search → save → watch next month.**

### What Daku deliberately is not

| Not this | Why |
|---|---|
| A video player | Jellyfin already does remux/transcode/subtitles. Rebuilding that is months of work. |
| A torrent client | qBittorrent exists, is mature, and has a Web API. |
| A media scraper | Jellyfin owns library metadata. Daku owns *acquisition*. |
| Internet-facing | No auth in v1. LAN only; never port-forward or expose it beyond the private network. |
| An Android app | The web UI is installed as a PWA. One codebase, no store, no port. |

---

## 2. Decision log

| # | Question | Answer | Consequence |
|---|---|---|---|
| 1 | Physical shape | Self-hosted server + web UI | One Python process, one SPA, phone is a client |
| 2 | Host | Linux (systemd, XDG) | `qbittorrent-nox`, PATH binaries, `systemd --user` |
| 3 | Telegram role | Bot with an "attach to entry" button → archive channel → downloadable later | Bot API, `copyMessage`, channel message index |
| 4 | Playback | Hand to Jellyfin | No ffmpeg pipeline, no codec matrix, no transcode cost |
| 5 | Engines | aria2c for HTTP, qBittorrent for torrents | Two adapters behind one queue |
| 6 | Stream while downloading | No | Simpler state machine; organise only on completion |
| 7 | Anime | Proper AniList/Jikan + mapping dataset | `anibridge-mappings` is a hard dependency |
| 8 | Library source | Only Daku downloads | Provenance guaranteed; no filename parsing |
| 9 | Users | Single user, LAN, no login | No auth tables, no permissions |
| 10 | Differentiator | One-tap flow from any device | One responsive UI; no separate mobile/desktop code path |
| 11 | Library vs download | Entries exist without files; magnets are saved independently | `wanted` intent flag, saved releases, explicit download-later action |
| 12 | Telegram large files | Local Bot API server; direct phone upload as fallback; MTProto deferred | See §4 "Telegram's 20 MB wall" |
| 13 | Watch state | `wanted` stays acquire intent; watch status is `planning/watching/completed/dropped/on_hold` plus per-episode progress | T-032 replaces the boolean watched plan; no Jellyfin watched sync in v1 |
| 14 | Metadata source priority | IMDb id is identity; Cinemeta → TheTVDB → AniList for detail, with a per-title override | T-022 adds the switcher; no IMDb scraping |
| 15 | Ratings | IMDb from Cinemeta; Rotten Tomatoes/Metacritic optional via an OMDb key | T-024; without a key the UI is IMDb-only, never an error |
| 16 | Anime releases | Nyaa.si as a second source (RSS only), merged with Torrentio | T-027; nyaa is never treated as a Torrentio provider |
| 17 | Torrent metadata | Standalone libtorrent session, metadata-only, no payload and no download row | T-030; qBittorrent remains the only torrent engine |
| 18 | Season packs | Match the episode and download only the matching file; ask when ambiguous | T-031; qBittorrent file priorities, never a silent full pack |
| 19 | Watch state | Local statuses `planning/watching/completed/dropped/on_hold` + per-episode progress | T-032 replaces the boolean watched plan in T-025; no sync in v1 |
| 20 | Visual identity | Orbitron headings only, 15px body, card hover glow instead of kind bars | T-033; STYLE.md is amended by the ticket |
| 21 | Title layout | Ratings/facts in a right rail on wide containers, stacked below 1024px | T-034; one DOM tree, container queries |
| 22 | Release provider defaults | Persist provider lists per media kind in SQLite; explicit searches override them | T-026; shared across browsers and restart-safe |

---

## 3. The four core flows

### A. Acquire a movie

```
Search "Dune Part Two"
  → IMDb suggestions (Cinemeta fallback)
  → pick tt15239678, kind=movie
  → POST /api/titles  (store metadata locally, no download)
  → title page → "Find release"
  → Torrentio /stream/movie/tt15239678.json
  → ranked list: 2160p 12.4 GB 👤 340 / 1080p 4.1 GB 👤 812 / ...
  → choose → POST /api/downloads {release_id}
  → qBittorrent add (savepath=downloads_dir, category=daku, tag=title_id)
  → worker polls → SSE pushes progress to UI
  → complete → ffprobe → hardlink/copy to Movies/... → Jellyfin refresh
  → "Play" deep-links to Jellyfin
```

### B. Acquire one episode

Same, except the title is a series, the episode picker selects `S02E05`, and Torrentio is
queried with `tt0944947:2:5`. Season packs are a separate explicit action.

### C. Save now, download later

```
Search "Perfect Blue" → add to library (no file)
  → "Find release" → pick 1080p BluRay
  → "Save" → POST /api/releases/{id}/save
       stores: info_hash, magnet, filename, quality, size, seeders,
               provider, parsed codec/audio hints, title_id + episode_id
       no download row, no qBittorrent entry, no disk usage
  → or paste a magnet → POST /api/releases {magnet, title_id}
       parse btih + dn + trackers, attach to the entry, save
  → weeks later: library shows "Saved · no file"
  → open title → Saved tab → "Download"
       POST /api/downloads {release_id} → normal queue → organise → Jellyfin
```

Saved magnets are re-checked against Torrentio (by `info_hash`) when the Saved tab is opened,
so seeders are current and dead releases are visibly marked, not silently broken.

### D. Telegram ingest

```
Forward video.mkv to @DakuBot
  → no caption? bot replies: [Link to library…] (web attach page, signed deep link)
  → caption "tt15239678" or /link dune  → inline keyboard of matches
  → tap → bot copyMessage(file_id) → archive channel
  → store (channel_id, message_id, file_id, title_id)
  → later, in Daku: "Download from Telegram"
  → local Bot API getFile → absolute path → copy into downloads_dir
  → same organiser as a torrent
```

Hard limit to state in the UI: **Daku can only see what the bot itself posted.** Bots cannot
read channel history. If you add files to the channel by hand, Daku does not know they exist.

---

## 4. Runtime architecture

```
┌───────────────────────────── Linux host ─────────────────────────────┐
│                                                                      │
│  daku (systemd --user, FastAPI + worker)                             │
│   ├─ REST /api/*  ──────────────┐                                    │
│   ├─ SSE /api/events            │                                    │
│   ├─ static SPA (webdist)       │                                    │
│   ├─ SQLite daku.db             │                                    │
│   ├─ metadata clients ──────────┼──► imdb / cinemeta / anilist / jikan│
│   ├─ Torrentio client ──────────┼──► torrentio.strem.fun             │
│   ├─ qbt adapter ───────────────┼──► 127.0.0.1:8080                  │
│   ├─ aria2 adapter ─────────────┼──► 127.0.0.1:6800                  │
│   ├─ telegram bot ──────────────┼──► 127.0.0.1:8081 (local API)     │
│   └─ jellyfin client ───────────┴──► 127.0.0.1:8096                  │
│                                                                      │
│  qbittorrent-nox        aria2c            telegram-bot-api           │
│  jellyfin (systemd)                                                  │
└──────────────────────────────────────────────────────────────────────┘
        ▲
        │ LAN, phone browser / PWA
```

### Backend

- Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2.0, SQLite (WAL), httpx, Pydantic v2.
- Worker: a single `asyncio` task every 2 s. It polls only *active* downloads, advances the
  state machine, and publishes events. No Celery, no Redis, no broker.
- SSE (`text/event-stream`) for progress; the UI falls back to 3 s polling if the stream drops.
- No background daemon threads. All I/O is async or `asyncio.to_thread`.

### Frontend

- React 18 + TypeScript + Vite + TanStack Query + Tailwind + React Router.
- One responsive layout, 320 px to ultrawide. No separate mobile/desktop modes, no breakpoint-
  specific navigation: the same tab bar works everywhere.
- Installable PWA (manifest + icons + `standalone`), no offline cache of media.
- Touch targets are comfortable for thumbs, but the layout is not designed "for mobile first".

### Storage

```
~/.config/daku/
  config.json
  secrets.json        (0600)
~/.local/share/daku/
  daku.db
  logs/daku.log
  mappings/anibridge.json.zst
```

Media never lives under `~/.local/share`. Defaults are `~/Media/Movies`, `~/Media/TV`,
`~/Media/Anime` and `~/Downloads/Daku`; all four are configurable and the media roots must be
Jellyfin library roots.

### Telegram's 20 MB wall — what it is and how to get around it

The cap applies **only to `getFile`**, i.e. pulling bytes down to this machine. It does not
apply to receiving a message, `copyMessage`, `forwardMessage`, or re-sending by `file_id`.
That is why attaching a 700 MB video to the archive channel works on the cloud API, and why
the attach flow is not affected. The wall appears only when Daku tries to materialise the file
locally.

| Option | Video size | Cost | Verdict |
|---|---|---|---|
| Cloud Bot API | ≤ 20 MB | none | default; fine for subtitles, clips, audio |
| **Local Bot API server** (`--local`) | download: no limit; upload: 2000 MB | second process, `api_id`/`api_hash`, one-time `logOut`, extra disk | **chosen for v1** |
| MTProto user session (Telethon/Pyrogram) | 2 GB, 4 GB premium; channel history; chunked reads | ToS/ban risk, session-string security, a different client stack | deferred |
| Phone → Daku direct upload | any size | none, but the file must be on the phone | fallback when the local server is absent |

**How the local server bypasses it**

1. Run `telegram-bot-api --local --http-port=8081 --dir=<storage> --api-id=<id>
   --api-hash=<hash>` on the same host as Daku (build from `tdlib/telegram-bot-api`, or run the
   container with identical host/container paths).
2. Point `telegram_api_base` at `http://127.0.0.1:8081`. Both `base_url` and `base_file_url`
   must use it.
3. `getFile` then returns an **absolute local path** in `file_path`. Daku copies that file; no
   HTTP download and no 20 MB check.
4. Uploads (`sendVideo`, `sendDocument`, `copyMessage` with a local path) go up to 2000 MB.

This is a **cap remover, not a streaming backend**. The bytes still come from Telegram's data
centres, so speed is whatever the DC and your uplink give you — no guarantee, and not suitable
for seek-and-play while downloading. T-017's local copy is a file copy from the bot server's
cache, not from Telegram, but `getFile` must first fetch and cache the file on that server.

**Operational caveats**

- `logOut` the bot from the cloud API once before switching:
  `GET https://api.telegram.org/bot<token>/logOut`. The same call is needed before switching
  back.
- The bot token is unchanged; only the API base moves.
- Budget roughly **2× the file size** in free disk during the transfer: the local server keeps
  its copy under `--dir`, and Daku writes its own copy under `downloads_dir`.
- Never expose port 8081 to the LAN or the internet; bind it to `127.0.0.1`.
- Daku and `telegram-bot-api` must share a host and a filesystem namespace. A Docker
  container with a different path mapping breaks the absolute-path trick.
- If the local server is not installed, Daku stays in `cloud` mode, refuses files > 20 MB with
  `TELEGRAM_SIZE_LIMIT`, and offers the direct-upload fallback instead of failing silently.

**Why not MTProto now:** it would also solve channel-history backfill and allow chunked
streaming, but it requires a real user account, carries a ban risk, and stores a session string
that is equivalent to a logged-in device. That is a much larger security decision than v1 needs.
It remains the clean upgrade path if Telegram becomes the primary source rather than an archive.

---

## 5. Data model (summary)

Full invariants live in `AGENTS.md` §5.

```
titles(id, kind, title, year, imdb_id, tmdb_id, tvdb_id, anilist_id, mal_id,
       overview, poster_url, added_at, wanted, wanted_at,
       watched, watched_at, metadata_source, metadata_synced_at)

episodes(id, title_id, season, episode, absolute, name, air_date, status,
         watched, watched_at)

releases(id, title_id, episode_id, provider, info_hash, magnet, filename,
         quality, size_bytes, seeders, metadata_json, is_saved, saved_at,
         last_checked_at, last_seen_seeders, miss_count, dead, fetched_at)

downloads(id, release_id, source, engine, external_id, status, progress,
          downloaded_bytes, total_bytes, speed_bps, eta_seconds, save_path, error)

media_files(id, download_id, title_id, episode_id, src_path, lib_path,
            size_bytes, container, vcodec, acodec, width, height, duration_s, is_primary)

telegram_messages(id, title_id, episode_id, channel_id, message_id, file_id,
                  file_unique_id, file_name, file_size, mime_type, state)

settings(key, value)
events(id, ts, level, kind, payload_json)
```

Three axes, deliberately separate:

| Axis | Values | Who writes it |
|---|---|---|
| **Availability** | `none` / `partial` / `complete` | derived from `media_files` |
| **Intent** | `saved` / `wanted` | user (`wanted` toggle) |
| **Watched** | `unwatched` / `watched` | user (episode toggles; title flag derived for series) |

A title can be `saved + none` (just a catalogue entry), `wanted + none` (queued, no release
chosen), `wanted + partial`, `saved + complete`, or `watched + none` (seen elsewhere but still
in the catalogue). `none` never means "error".

---

## 6. Phases and tickets

Tickets live in `tickets/`. Each is independently shippable and lists acceptance criteria.

### Phase 0 — Skeleton (runs, stores, reports)

| Ticket | Deliverable |
|---|---|
| T-001 | Repo, config, logging, health, doctor stub, SPA shell |
| T-002 | SQLite schema + migration runner + repositories |

### Phase 1 — Search and library

| Ticket | Deliverable |
|---|---|
| T-003 | IMDb/Cinemeta search + title detail normalisation |
| T-004 | AniList adapter for anime (Jikan deferred) |
| T-005 | AniBridge ID + episode mapping ingestion |
| T-006 | Library CRUD API and mobile library UI (entries with no files) |

### Phase 2 — Releases and downloads

| Ticket | Deliverable |
|---|---|
| T-007 | Torrentio adapter, parsing, ranking, resolved metadata |
| T-008 | Saved magnets, manual magnets, download-later |
| T-009 | qBittorrent adapter + download queue |
| T-010 | aria2 adapter for direct URLs |
| T-011 | SSE progress + downloads UI |

### Phase 3 — Organise and play

| Ticket | Deliverable |
|---|---|
| T-012 | ffprobe inspection and primary-file selection |
| T-013 | Organiser + Jellyfin naming + hardlink/copy |
| T-014 | Jellyfin scan, item resolution, play links |

### Phase 4 — Telegram

| Ticket | Deliverable |
|---|---|
| T-015 | Bot process, allowlist, archive-channel config |
| T-016 | Attach-to-entry flow + channel mirroring + inbox UI |
| T-017 | Download from Telegram via local Bot API (2 GB), 20 MB fallback |

### Phase 5 — Ship

| Ticket | Deliverable |
|---|---|
| T-018 | PWA polish, search-as-you-type, empty/error states |
| T-019 | Linux install, `systemd --user`, first-run wizard |
| T-020 | Hardening: retries, disk guard, backups, activity log |

### Phase 6 — Refinement (user-requested)

| Ticket | Deliverable |
|---|---|
| T-021 | Search preview page, Add from preview, season tabs + full episode list |
| T-022 | Metadata re-hydration, per-title source switcher, TVDB fallback (fixes Cyberpunk) |
| T-023 | Minimum quality, provider toggles, sort and Show more above releases |
| T-024 | IMDb/Rotten Tomatoes ratings, genres/runtime/cast, expandable description |
| T-025 | Toasts and notice polish (watched state moved to T-032) |

### Phase 7 — Control and sources

| Ticket | Deliverable |
|---|---|
| T-026 | Settings menu, persisted per-kind provider defaults |
| T-027 | Nyaa.si as a second anime release source |
| T-028 | Conservative anime classification from the AniBridge mapping |
| T-029 | Manual metadata fixer: kind, identity and episode mapping |

### Phase 8 — Torrent intelligence

| Ticket | Deliverable |
|---|---|
| T-030 | Magnet metadata probe, file list, copy magnet |
| T-031 | Episode file matching and selective download from packs |

### Phase 9 — Tracking and identity

| Ticket | Deliverable |
|---|---|
| T-032 | Local watch-status tracker and per-episode progress |
| T-033 | Visual refresh: typography, card glow, rating colours |
| T-034 | Two-column title detail with a facts rail |
| T-035 | Poster-derived dithered banner and library backdrop |

### Phase 10 — Anime fidelity and UI fixes

| Ticket | Deliverable |
|---|---|
| T-036 | Anime episode titles and air dates from Jikan/TVDB/AniList |
| T-037 | Aggregate season structure (specials, split cours) for anime |
| T-038 | Providers popover that escapes episode/release clipping |

T-021 and T-023 are the two halves of "search → pick → watch"; T-022 is the repair path for
entries added while metadata was offline. Phase 7 makes classification and provider selection
honest; Phase 8 makes the acquisition side understand what a release actually contains; Phase 9
is presentation and personal tracking.

**Dependency graph**

```
T-001 → T-002 → T-003 → T-004 → T-005
                  │       └──────┐
                  ├→ T-006 ──────┤
                  └→ T-007 → T-008
                        │
                        └→ T-009 → T-010 → T-011
                              │
                              └→ T-012 → T-013 → T-014
T-015 → T-016 → T-017            (T-017 also needs T-010, T-013)
T-006, T-011 → T-018
all core → T-019, T-020

T-023 → T-026 → T-027
T-005 → T-028 → T-029 (also needs T-021, T-022)
T-008 → T-030 → T-031 (also needs T-009, T-012, T-013)
T-021 → T-032
T-024 → T-033 → T-034, T-035
T-021 → T-038 (also needs T-023)
T-028, T-029 → T-036 → T-037 (T-036 also needs T-004, T-021)
T-005, T-022, T-028, T-029 → T-037
```

Note: T-008 depends on T-006 *and* T-007, and is usable before T-009. You can build a
"saved magnets" library with no download engine at all — that is the M2 milestone.

**Milestones**

- **M1 — It runs:** T-001, T-002. Health page green, DB created.
- **M2 — Catalogue + saved magnets:** T-003 → T-008. Search, add without downloading, save
  magnets, browse.
- **M3 — I can download:** T-009 → T-011. Pick a saved release, watch it download.
- **M4 — It appears in Jellyfin:** T-012 → T-014. Play from the phone.
- **M5 — Telegram loop works:** T-015 → T-017.
- **M6 — Installable:** T-018 → T-020.

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Torrentio changes shape or blocks | Search dead | Adapter + 1 h cache + stale fallback + fixtures from a recorded response. Swap provider behind `base.py` |
| Torrentio rate limits on saved-tab refresh | Slow UI | Refresh at most 1 request per saved release per 10 min; batch by title; degrade to cached seeders |
| Saved magnet goes dead | "Download" fails later | Re-check by `info_hash`; show `last_checked_at` + seeders; mark `dead` after 3 consecutive misses; keep the row |
| Telegram 20 MB cap | Attach flow useless for real video | Local Bot API server is a first-run prerequisite, clearly documented. Detect cloud mode and offer direct phone upload as fallback |
| Local Bot API needs `logOut` + api_id/hash | Confusing setup | Doctor check + guided setup screen with the exact commands |
| Bots cannot read channel history | User expects backfill | UI copy states it. All ingest goes through the bot |
| Anime ID/episode mismatch | Wrong file for episode | `anibridge-mappings` + explicit "fix mapping" UI; never silently guess |
| Jellyfin naming mismatch | Item never appears | Golden tests over naming; poll `/Items` after scan; surface unmatched files |
| Hardlink across drives | Copy of 40 GB | `st_dev` check, configurable `organize_mode`, disk-space guard before start |
| qBittorrent not running | No torrents | `qbittorrent-nox` as a user unit; doctor detects and offers the exact `systemctl` command |
| Torrent completion before flush | Corrupt organise | Wait for stable size for 5 s, then ffprobe |
| Legal exposure | Hosting/streaming | Daku never serves media; Jellyfin is the user's own server; disclaimer |

**Highest-risk ticket:** T-005 (anime mapping). It should not block M2–M4. Ship movies and TV
first, keep anime behind a `kind=anime` path that is enabled once T-005 is green.

---

## 8. Open questions (answer before T-019)

1. **Media roots** — are Movies/TV/Anime on the same filesystem as `downloads_dir`? If not,
   hardlinks are impossible and the default must be `copy`.
2. **qBittorrent layout** — is `savepath` per-category configured, or should Daku always pass an
   explicit `savepath`? Plan assumes explicit.
3. **Archive channel** — one channel for everything, or one per kind? Plan assumes one; the
   schema supports more later.
4. **Telegram local server** — acceptable to require a second systemd unit and an `api_id`?
   If not, the Telegram feature is capped at 20 MB and should be descoped.
5. **Anime filing** — file anime under `TV/` or a separate `Anime/` Jellyfin library? Plan
   assumes separate, named by the TVDB/TMDB provider structure.
6. **Saved-magnet retention** — keep dead releases forever, or auto-archive after N failed
   checks? Plan keeps them, marked `dead`, until the user removes them.

---

## 9. v1 release criteria

- [ ] Search a movie and a series, add both to the library **without downloading anything**, and
      see them after a restart.
- [ ] A library entry with no file renders as "Saved", not "missing" or "error".
- [ ] Find a release, save it as a magnet, restart Daku, and download it later from the Saved tab.
- [ ] Paste a manual magnet, attach it to an entry, and download it later.
- [ ] Find a release for a movie and for a specific episode; save one as a recent option.
- [ ] Start a download; see progress, speed and ETA update live in the browser.
- [ ] On completion the file is organised, Jellyfin scans, and the Play button opens the item.
- [ ] Forward a 700 MB file to the bot, attach it to an entry, mirror it to the channel, and
      download it back through Daku.
- [ ] `./install.sh` on a fresh Linux install sets up the service and wizard, and
      `systemctl --user enable --now daku` survives a reboot.
- [ ] `daku` survives a reboot, a killed qBittorrent, and a Torrentio outage without losing state.

---

## 10. Deliberately deferred

Sequential streaming, in-app player, subtitle search, multi-user, remote access, seeding
schedules, RSS watchlists, auto-download of saved magnets when they appear, *arr import,
Docker image, Windows/macOS packaging, MTProto user account, channel-history backfill,
notifications.
