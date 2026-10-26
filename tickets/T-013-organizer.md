# T-013 — Organiser: naming, hardlink, library placement

**Phase:** 3 · **Depends on:** T-012 · **Estimate:** L

## Goal

Turn a completed, inspected download into a correctly named file inside the Jellyfin library,
without copying tens of gigabytes when a hardlink is possible.

## Scope

- `services/organize/naming.py`: pure functions producing relative paths for movie, series
  and anime. No I/O.
- `services/organize/organizer.py`: plan → validate → link/copy → verify → persist.
- `services/organize/sanitize.py`: filesystem-safe names, length limits, portable reserved names.
- Worker integration: `complete → organizing → available | failed`.
- `POST /api/downloads/{id}/organize` (retry) and `GET /api/downloads/{id}/plan` (dry run).
- Episode assignment for packs: map files to episodes by `SxxEyy` in the filename, then by
  order; surface anything unresolved for manual assignment.

## Out of scope

Jellyfin API calls (T-014), subtitle conversion, renaming inside the torrent directory.

## Acceptance criteria

- [ ] Movie lands at
      `Movies/Name (Year) [imdbid-tt0000000]/Name (Year) [imdbid-tt0000000].mkv`.
- [ ] Episode lands at
      `TV/Series (Year) [imdbid-tt0000000]/Season 01/Series (Year) - S01E02 - Ep Title.mkv`.
- [ ] Anime lands under `anime_dir` using the **provider's** season/episode, with `Season 00`
      for specials. The absolute episode number is never written into `SxxEyy`.
- [ ] Hardlink is used when `downloads_dir` and the library root share a device; otherwise
      copy; `organize_mode` can force `copy` or `move`.
- [ ] The source file is never deleted unless `organize_mode="move"` or the caller passes
      `delete_source=true` after a verified copy.
- [ ] An existing destination with the same size is reused (idempotent); a different size
      gets a ` (2)` suffix rather than being overwritten.
- [ ] Names are sanitised: `<>:"/\|?*` removed, trailing dots/spaces stripped, reserved
      Windows-reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`) suffixed so the
      library stays portable, full path under 240 characters.
- [ ] `media_files.lib_path` is set and the state becomes `available` only after the file
      exists at the destination with the expected size.
- [ ] A permission or cross-device failure leaves the source intact and sets `failed`
      with a retryable error code.
- [ ] Dry-run plan returns source, destination and the operation without touching disk.

## Implementation notes

Naming must be deterministic and unit-tested as strings. Use `[imdbid-tt...]` on both the
folder and the file; Jellyfin recommends it and it prevents wrong matches.

Series folder year: prefer the series start year. If unknown, omit `(year)` rather than guess.
Episode title: sanitise; if empty, omit the ` - Title` segment.

Season padding: `Season {season:02d}`. Episode: `S{season:02d}E{episode:02d}`.

Hardlink: `os.link(src, dst)` inside `asyncio.to_thread`. Compare `os.stat(src).st_dev` with
`os.stat(library_root).st_dev` first; if linking raises `EXDEV` or `EPERM`, fall back to
`shutil.copy2` (with progress) and verify size + mtime.

Path traversal guard: resolve the destination and assert it is under the configured library
root before writing anything.

Create `Season NN` only when needed. Do not create empty season folders.

## Test plan

- `tests/organize/test_naming.py`: golden strings for movie, series, anime, specials,
  missing year, missing episode title, long titles, reserved names, Unicode.
- `tests/organize/test_organizer.py` (tmp_path): hardlink, copy fallback on `EXDEV`,
  idempotent re-run, collision suffix, permission failure, traversal attempt blocked.
- `tests/organize/test_pack.py`: files with `S01E01..E06` map correctly; ambiguous pack is
  reported unresolved.
