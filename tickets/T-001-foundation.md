# T-001 — Foundation: repo, config, logging, health

**Phase:** 0 · **Depends on:** — · **Estimate:** S

## Goal

`uv run daku` starts a FastAPI server on `0.0.0.0:8420` that serves a placeholder SPA and a
working `/api/health`. Config resolves from environment → JSON → defaults. Logs are JSON lines.

## Scope

- `pyproject.toml` (uv), `ruff`, `mypy`, `pytest`, `.gitignore`, `README.md` stub.
- `src/daku/config.py`: `Settings` (Pydantic), XDG-aware paths, `secrets.json` loader,
  `0600` permissions, config resolution order.
- `src/daku/logging_setup.py`: JSON formatter, rotating file handler in `data_dir/logs/`.
- `src/daku/app.py` + `__main__.py`: app factory, CORS off, static SPA mount, lifespan hooks.
- `web/`: Vite + React + TS + Tailwind shell with one route and a health card.
- Routers: `GET /api/health`, `GET /api/system/doctor` (stub returning `not_configured` checks).
- `packaging/daku.service` placeholder for the systemd user unit.

## Out of scope

Database, any external integration, authentication, PWA install.

## Acceptance criteria

- [x] `uv run daku` binds `0.0.0.0:8420` and `GET /api/health` returns
      `{status:"ok", version, uptime_s, data_dir}`.
- [x] `GET /api/system/doctor` returns one entry per integration
      (`qbittorrent, aria2, jellyfin, telegram, ffprobe, disk`) with `status` and `detail`.
- [x] Config precedence is env → `config.json` → default, proven by a unit test.
- [x] `data_dir` and `data_dir/logs` are created on first run.
- [x] `secrets.json` is created `0600` and never appears in logs or `/api/health`.
- [x] `pnpm -C web build` places the SPA in `src/daku/webdist/` and `/` renders it.
- [x] `uv run ruff check .` and `uv run mypy src` pass.

## Implementation notes

- Defaults: config `~/.config/daku`, data `~/.local/share/daku`, honouring `XDG_CONFIG_HOME`
  and `XDG_DATA_HOME`.
- Bind host/port are settings, not constants, so tests can use `127.0.0.1:0`.
- Uvicorn should run with `log_config=None` so our handler owns the output.
- Serve the SPA with a catch-all that excludes `/api/*`; return `index.html` for unknown
  non-API paths so client routing works.

## Test plan

- `tests/test_config.py`: precedence, XDG path resolution, secret redaction, `0600` mode.
- `tests/test_health.py`: `TestClient` hits `/api/health` and `/api/system/doctor`.
- `tests/test_spa.py`: `/` returns HTML, `/api/nope` returns 404 JSON, not HTML.

## Completion notes

- Python is pinned to 3.12 via `.python-version`; `uv sync` provisions it.
- The SPA is Vite 6 + React 18 + Tailwind 4 and emits into `src/daku/webdist/`.
  `pnpm` 12 was installed user-locally (`~/.local/bin/pnpm`); the build also works with npm.
- Doctor integration entries are configuration stubs until T-009 (qBittorrent), T-010 (aria2),
  T-014 (Jellyfin) and T-015 (Telegram). `ffprobe` and `disk` are live checks.
- `app.py` exposes a module-level `app` for `uvicorn daku.app:app`; `uv run daku` uses the
  factory with resolved settings for the bind host/port.
