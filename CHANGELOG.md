# Changelog

All notable changes to Daku are recorded here. The project follows
[Semantic Versioning](https://semver.org/) and pre-release versions use the PEP 440 form
(`0.1.0a1` is the first alpha of `0.1.0`).

## [0.1.0a2] — 2026-11-07

Second alpha. Release discovery is broader and configurable per media kind, the title page
has a dedicated release-options flow, Downloads is rebuilt around honest status cards, and
first-run installs now have Linux/Windows scripts plus a portable bundle.

### Added

- Continue Watching now includes freshly downloaded, never-played titles. A series shows its
  earliest unwatched downloaded episode, a movie its primary file, and `Up next` skips
  episodes marked watched.
- Torrents-CSV as a movie/TV release source alongside Torrentio, with local magnet building,
  one-request-per-second throttling, cached responses and stale fallback.
- A release-source registry with per-kind source settings. `torrents-csv` is selectable for
  movies/series; AnimeTosho remains anime-only; legacy `anime_sources`/`nyaa` values still
  read correctly.
- Episode master–detail navigation and a three-level Find/Saved/release-options flow, with
  device-back handling and a dedicated release sub-menu.
- Downloads page split into Needs attention / Downloading / Completed, grouped by title, with
  one card hierarchy, labelled fetching/library sizes and no dead file-list links.
- `install.sh` / `uninstall.sh` for an idempotent Linux `systemd --user` install.
- `install.ps1` for native Windows: installs `uv` when missing, builds the SPA, writes config
  only when absent and registers an at-logon Scheduled Task.
- `scripts/build_bundle.py` and `packaging/daku.spec` build a self-contained PyInstaller
  bundle and zip, with `start-daku.cmd` / `start-daku.sh` launchers.
- A settings surface for per-kind release sources and provider defaults.

### Changed

- Torrentio now defaults to **all providers** with no upstream quality filter; the local
  filter stays authoritative, and unlabelled remux rows are no longer discarded.
- Release searches expose per-kind source selection rather than an anime-only toggle.
- Download cards no longer show selection pills or already-downloaded accent chips; sizes
  now distinguish what is being fetched from what is already in the library.

### Fixed

- The libtorrent magnet probe rebuilds its DHT session after a long host suspend or a
  timeout/failure, instead of timing out until the process restarts.
- Download inspection is scoped to the torrent's own files.
- qBittorrent file selection and download-later behaviour remain enforced through the
  stop → priorities → verify → start path.

## [0.1.0a1] — 2026-11-01

First alpha release. Daku is usable end to end for search → save → download → watch on a
LAN, but still missing the organiser, Jellyfin and Telegram integrations (see the roadmap
in the README).

### Added

- Library entries independent of downloads, with wanted/downloaded/no-file filters, watch
  statuses and toasts.
- Metadata search through IMDb suggestions and Cinemeta, anime metadata through AniList and
  Jikan, AniBridge provider mappings, split-cour season/episode ranges and alias inventory.
- Manual metadata fixer for kind, identity, aliases and episode renumbering, persisted as
  overrides that survive refreshes.
- AniList identity validation that rejects manga ids and repairs dead artwork, with a
  TVDB/AniList source per title.
- Release discovery on Torrentio for movies/TV and AnimeTosho's JSON feed for anime, with
  per-kind provider defaults, quality filters, dedupe, sorting and stale-cache fallback.
- Multi-alias, multi-notation release search with parsed-filename and provider-season
  episode matching. Season packs are badged and never silently attached to one episode.
- Saved releases, manual magnets and an optional libtorrent metadata probe for torrent file
  listings.
- qBittorrent Web API v2 engine (4.x through 5.2+) with stop → priorities → verify → start
  file selection, and an aria2c JSON-RPC engine for direct URLs.
- Live download progress over SSE, per-file progress, retry, and queue removal that keeps
  library files. Destructive removal refuses to delete the only copy.
- Availability computed from files that exist, missing-file reporting and a stale-media-row
  reconcile action.
- Downloaded-files carousel, full files page, ffmpeg thumbnails and per-season grouping.
- Host mpv playback from a file card, one tracked child process, and read-only playback
  progress from mpv's JSON IPC socket with a seek bar on file cards.
- Settings page for playback and release provider defaults, and a `daku doctor` command that
  probes every integration without side effects.
- Responsive React/Vite SPA with SSE live updates, served by the same FastAPI process.
- SQLite storage in WAL mode with append-only migrations through schema version 13.

### Known limitations

- No organiser yet: finished downloads stay in `downloads_dir`; Jellyfin library folders are
  not populated by Daku.
- No Jellyfin scan, Telegram bot/archive, PWA install or real systemd installer.
- No authentication. LAN use only.
- AnimeTosho is rate-limited to one request per second per process.

[0.1.0a2]: https://github.com/example/daku/releases/tag/v0.1.0a2
[0.1.0a1]: https://github.com/example/daku/releases/tag/v0.1.0a1
