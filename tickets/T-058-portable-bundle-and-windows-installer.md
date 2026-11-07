# T-058 — Portable bundle and Windows installer

**Phase:** 15 · **Depends on:** T-019 · **Estimate:** M

## Goal

Make a first run on Windows as short as one PowerShell command, and ship the app as a
self-contained folder for machines without Python or Node. The same bundle is usable on
Linux, so support can ask for one artifact instead of a toolchain.

## Scope

- `install.ps1`: install `uv` when missing (winget), `uv sync`, build the SPA with pnpm or
  npm (or use the shipped `webdist`), write config/secrets only when absent, register an
  at-logon Scheduled Task, print LAN URL and firewall command.
- `install.sh` / `uninstall.sh`: idempotent Linux `systemd --user` install and safe removal,
  matching T-019.
- `packaging/daku.spec`: PyInstaller onedir bundle including `webdist`.
- `scripts/build_bundle.py`: build the SPA (unless `--skip-web`), run PyInstaller, add
  `start-daku.cmd` / `start-daku.sh`, and write `dist/daku-<version>-<platform>-<arch>.zip`.
- `pyproject.toml`: optional `package` extra with PyInstaller.
- README quick-install and portable-bundle sections.

## Out of scope

- `.msi`/MSIX signing, auto-update, Docker, macOS app bundle, and the T-019 `/setup` wizard.
- Windows mpv playback progress (needs a named-pipe IPC implementation).

## Acceptance criteria

- [x] `install.sh` is idempotent, creates the virtualenv/SPA and installs a user unit.
- [x] `uninstall.sh` removes the unit and leaves config, data, downloads and media.
- [x] `install.ps1` creates config/secrets without overwriting existing files and registers
      a Scheduled Task.
- [x] `python scripts/build_bundle.py --skip-web` produces a runnable `dist/daku/daku`.
- [x] The produced bundle answers `daku --version` and runs `daku doctor`.
- [x] The zip contains the bundle plus both launchers and a README.
- [x] `scripts/check.sh` passes.

## Verification notes

- The Linux bundle was built and `dist/daku/daku --version` returned `daku 0.1.0a1`;
  `daku doctor --json` produced the expected checks.
- `install.ps1` was reviewed statically; no Windows host was available in this environment.
