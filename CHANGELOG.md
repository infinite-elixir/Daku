# Changelog

All notable changes to Daku are recorded here. The project follows
[Semantic Versioning](https://semver.org/) and pre-release versions use the PEP 440 form
(`0.1.0a1` is the first alpha of `0.1.0`).

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

[0.1.0a1]: https://github.com/example/daku/releases/tag/v0.1.0a1
