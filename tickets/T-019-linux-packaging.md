# T-019 — Linux install, systemd, first run

**Phase:** 5 · **Depends on:** all core tickets · **Estimate:** L

## Goal

A plain Linux box (Ubuntu/Debian family first) gets Daku from a clone to an always-on service
in one idempotent script, with a browser wizard and no root-owned media.

## Scope

- `install.sh` (idempotent):
  - check `python3.12+`, `uv`, `ffmpeg`, `aria2c`, `qbittorrent-nox`; report missing ones with
    the exact `apt` command, never silently install
  - create `~/.config/daku` (0700 config/secrets) and `~/.local/share/daku`
  - `uv sync --frozen`, build the SPA if `pnpm` is present, otherwise use the shipped `webdist`
  - write `config.json` only if absent; never overwrite
  - install `packaging/daku.service` to `~/.config/systemd/user/daku.service`
  - `systemctl --user daemon-reload && systemctl --user enable --now daku`
  - `loginctl enable-linger "$USER"` if not already enabled
  - print the LAN URL and a one-line "do not expose this to the internet" warning
- `packaging/daku.service`: `Restart=on-failure`, `RestartSec=3`, `Environment=PYTHONUNBUFFERED=1`,
  no `User=` (it is a user unit), working directory set, journal logging.
- `uninstall.sh`: stop, disable, remove the unit and `daemon-reload`; leave config, data and media.
- First-run wizard at `/setup`: media roots, downloads dir, qBittorrent, Jellyfin, Telegram,
  dependency checks, disk summary. Same checks as `doctor`.
- Optional: a self-contained PyInstaller `--onedir` ELF in `dist/daku/` for machines without
  Python. Not the default install path.

## Out of scope

Docker image, `.deb` packaging, AUR, Windows/macOS installers, auto-update.

## Acceptance criteria

- [ ] On a clean Ubuntu 24.04 or Debian 12 VM with systemd, `./install.sh` completes and
      `systemctl --user is-active daku` returns `active`.
- [ ] The wizard opens at `http://localhost:8420/setup` and each enabled integration can be
      tested before finishing.
- [ ] After a reboot with no login, the service is running (linger enabled) and the UI loads.
- [ ] `journalctl --user -u daku -f` shows structured logs; no `sudo` is required to run Daku.
- [ ] `install.sh` is safe to run twice: it does not duplicate the unit, overwrite `config.json`
      or reset the database.
- [ ] Missing `ffmpeg`, `aria2c` or `qbittorrent-nox` produces a readable list with the exact
      install command, and the app still starts in a degraded state.
- [ ] `uninstall.sh` removes the unit and leaves `~/.config/daku`, `~/.local/share/daku`,
      downloads and the media library untouched.
- [ ] The setup summary shows config, data, download and media paths for backup.
- [ ] The installer prints the LAN address (`hostname -I` / `ip route`) and never opens a port
      beyond `8420` on the local network.
- [ ] Optional ELF bundle starts on a machine with no Python installed.

## Implementation notes

Linger is the piece people forget: `loginctl enable-linger "$USER"` is what makes a
`systemd --user` unit start at boot without an interactive login.

User units cannot reliably order against `network-online.target`; rely on
`Restart=on-failure` + `RestartSec=3` rather than a hard dependency. The first attempt after
boot may fail once while the network settles, and that is acceptable.

Do not install packages automatically. Print:

```
sudo apt install ffmpeg aria2 qbittorrent-nox
```

Jellyfin has its own repository; link to it instead of guessing a package name.

`uv sync --frozen` requires a committed `uv.lock`. Build the SPA in CI/release and ship
`webdist/` so a source install does not need Node.

The unit should use the venv entrypoint directly:

```ini
[Service]
WorkingDirectory=%h/daku
ExecStart=%h/daku/.venv/bin/daku
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
```

Bind `0.0.0.0:8420` is intentional for LAN access. There is no auth; the installer must say so.

## Test plan

- `shellcheck install.sh uninstall.sh`; `systemd-analyze verify packaging/daku.service`.
- Clean-VM checklist committed as `docs/linux-smoke-test.md` (install, wizard, reboot, uninstall).
- `tests/test_portable.py`: XDG path resolution and `--portable`-style override if kept.
- `tests/test_single_instance.py`: second launch exits with a clear message.
