# Daku

Daku is a self-hosted, single-user, LAN-only media librarian for Linux. It finds titles,
saves releases, downloads them with qBittorrent or aria2c, files them into a Jellyfin
library, and mirrors library entries to a Telegram archive channel.

Daku does not play video. Jellyfin owns playback.

## Status

Phase 0 — foundation and database. `uv run daku` starts the API and SPA on
`http://0.0.0.0:8420`.

## Development

```bash
uv sync
uv run daku
uv run pytest -q
uv run ruff check . && uv run mypy src

pnpm -C web install
pnpm -C web build      # emits into src/daku/webdist/
```

`daku db init`, `daku db migrate` and `daku db dump` manage the SQLite database.

## Disclaimer

Daku indexes public metadata and hands magnets to a torrent client. It hosts no content.
Only download material you have the right to access.
