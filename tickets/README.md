# Daku implementation tickets

Work them in order. Each ticket is shippable on its own and lists acceptance criteria.
`plan.md` holds the product decisions; `AGENTS.md` holds the constraints and integration
contracts. If a ticket contradicts `AGENTS.md`, `AGENTS.md` wins and the ticket is wrong.

| Ticket | Phase | Depends on | Delivers |
|---|---|---|---|
| [T-001](T-001-foundation.md) | 0 | — | Repo, config, logging, health, SPA shell |
| [T-002](T-002-database.md) | 0 | T-001 | SQLite schema, migrations, repositories |
| [T-003](T-003-metadata-search.md) | 1 | T-002 | IMDb/Cinemeta search |
| [T-004](T-004-anime-metadata.md) | 1 | T-003 | AniList + Jikan |
| [T-005](T-005-anime-mapping.md) | 1 | T-004 | AniBridge ID/episode mapping |
| [T-006](T-006-library.md) | 1 | T-003 | Library CRUD + mobile UI |
| [T-007](T-007-torrentio.md) | 2 | T-003 | Torrentio search + ranking |
| [T-008](T-008-saved-releases.md) | 2 | T-006, T-007 | Saved + manual magnets, download later |
| [T-009](T-009-qbittorrent.md) | 2 | T-007 | qBittorrent engine + queue |
| [T-010](T-010-aria2.md) | 2 | T-009 | aria2 direct downloads |
| [T-011](T-011-live-progress.md) | 2 | T-009, T-010 | SSE progress + downloads UI |
| [T-012](T-012-ffprobe.md) | 3 | T-009 | Media inspection, primary file |
| [T-013](T-013-organizer.md) | 3 | T-012 | Jellyfin naming + hardlink/copy |
| [T-014](T-014-jellyfin.md) | 3 | T-013 | Scan, item resolution, play links |
| [T-015](T-015-telegram-bot.md) | 4 | T-001 | Bot, allowlist, archive config |
| [T-016](T-016-telegram-attach.md) | 4 | T-006, T-015 | Attach-to-entry + channel mirror |
| [T-017](T-017-telegram-download.md) | 4 | T-010, T-013, T-016 | Download from Telegram (2 GB) |
| [T-018](T-018-pwa.md) | 5 | T-006, T-011 | PWA, search-as-you-type, polish |
| [T-019](T-019-linux-packaging.md) | 5 | core | Linux install, systemd, first run |
| [T-020](T-020-hardening.md) | 5 | all | Retries, disk guard, backups |
| [T-021](T-021-search-preview-and-episodes.md) | 6 | T-003, T-004, T-006 | Search preview, Add from preview, season/episode navigation |
| [T-022](T-022-metadata-recovery.md) | 6 | T-003, T-004, T-021 | Metadata re-hydration, source switcher, TVDB fallback |
| [T-023](T-023-release-discovery-controls.md) | 6 | T-007 | Min quality, providers, sort, show more |
| [T-024](T-024-ratings-and-rich-metadata.md) | 6 | T-021, T-003 | IMDb/RT ratings, genres/runtime/cast, expandable overview |
| [T-025](T-025-watched-and-toasts.md) | 6 | T-006, T-021 | Toasts and notice polish (watched state moved to T-032) |
| [T-026](T-026-settings-and-provider-defaults.md) | 7 | T-023 | Settings menu, per-kind provider defaults |
| [T-027](T-027-nyaa-source.md) | 7 | T-007, T-023, T-026 | Nyaa.si anime release source |
| [T-028](T-028-anime-classification.md) | 7 | T-003, T-004, T-005 | Anime classification |
| [T-029](T-029-manual-metadata-fixer.md) | 7 | T-021, T-022, T-028 | Manual identity and episode-map fixer |
| [T-030](T-030-magnet-inspection.md) | 8 | T-007, T-008 | Magnet metadata probe, file list, copy link |
| [T-031](T-031-episode-file-selection.md) | 8 | T-009, T-012, T-013, T-030 | Episode file matching, selective download |
| [T-032](T-032-watch-status-tracker.md) | 9 | T-006, T-021 | Planning/watching/completed tracker |
| [T-033](T-033-visual-refresh.md) | 9 | T-018, T-024 | Caskaydia-only type scale, card glow, rating colours |
| [T-034](T-034-title-detail-layout.md) | 9 | — | ~~Two-column title page with facts rail~~ (superseded by T-045) |
| [T-035](T-035-dithered-art.md) | 9 | T-018, T-024, T-033 | **Reverted** — dithered banner/backdrop removed; direct banner art |
| [T-036](T-036-anime-episode-titles.md) | 10 | T-004, T-021, T-028, T-029 | Anime episode titles + air dates |
| [T-037](T-037-anime-season-structure.md) | 10 | T-005, T-022, T-028, T-029, T-036 | Aggregate anime season structure |
| [T-038](T-038-providers-popover-clipping.md) | 10 | T-021, T-023 | Unclipped providers popover |
| [T-039](T-039-remove-from-download-queue.md) | 11 | T-011, T-031 | Remove a download from the queue |
| [T-040](T-040-qbittorrent-5x-compatibility.md) | 11 | T-009 | qBittorrent 5.x add response and start/stop |
| [T-041](T-041-enforce-selective-downloads.md) | 11 | T-031, T-040 | Enforce selective downloads (T-031 regression) |
| [T-042](T-042-episode-from-finished-pack.md) | 11 | T-031, T-039, T-041 | Download a later episode from an already-finished pack |
| [T-043](T-043-spacing-tokens.md) | 12 | T-018 | Spacing tokens and app-wide density |
| [T-044](T-044-downloads-page-redesign.md) | 12 | T-039, T-042, T-043 | Downloads page: sections, grouped cards, honest sizes |
| [T-045](T-045-title-hero-layout.md) | 12 | T-024, T-043 | Poster-matched title hero (supersedes T-034) |
| [T-046](T-046-release-panel.md) | 12 | T-030, T-031, T-043, T-045 | Taller release panel, single action row |
| [T-047](T-047-downloaded-files.md) | 12 | T-014, T-043, T-044, T-045 | Downloaded-files carousel, files page, thumbnails, library filter |
| [T-048](T-048-mpv-playback.md) | 12 | T-043, T-044, T-047 | Launch a downloaded file in the host's mpv |

## Rules

- Do not start a ticket whose dependency is not green.
- Network tests use `respx`; never hit IMDb, Torrentio, nyaa.si, Jellyfin or Telegram in CI.
  libtorrent probes are faked in tests, never run against a real swarm.
- Tick acceptance boxes in the ticket when done and note anything deferred.
- New work discovered mid-ticket becomes a new ticket, not an unreported TODO.
