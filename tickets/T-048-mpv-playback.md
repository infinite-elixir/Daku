# T-048 — Play a downloaded file in mpv

**Phase:** 12 · **Depends on:** T-043, T-044, T-047 · **Estimate:** M

## Decision record

This ticket **amends AGENTS.md §1 "Playback"**. Daku still owns no decoding, no transcoding
and no in-app player: it launches the host's own mpv on explicit request and hands it a path
that Daku itself filed. Jellyfin stops being the playback path in the UI and keeps scanning,
metadata and library management; no Jellyfin play URL is rendered by Daku.

Consequences accepted in the design review:

- The service is LAN-only and unauthenticated, so the endpoint that spawns a process is
  reachable by every device on the LAN. It therefore accepts **only** a media-file id, never
  a path or an argument.
- Because the launch happens on the server host, clicking from a phone opens a window on a
  machine the phone cannot see. That is accepted; the UI is a single-user LAN surface.
- A file card's click meaning depends on the `mpv_enabled` setting (launch vs navigate).
  Accepted as a trade for one obvious click target, with the scroll guard below.

## Problem

Files can be downloaded, filed, thumbnailed and listed, but there is no way to watch them
from Daku. Every click ends in a browser download prompt or nothing at all.

## Scope

### Settings

- `mpv_enabled: bool = false` (config file / env, exposed read-only to the UI).
- `mpv_bin: str = "mpv"` — a bare command resolved with `shutil.which` or an absolute path.
- Both appear on the Settings page: a toggle and a path field with the resolved binary
  shown underneath.
- Default off: the feature stays invisible until the user turns it on.

### API

`POST /api/media-files/{id}/play`

- Body: empty. Nothing from the client is interpolated into argv.
- Resolve the row, take `lib_path` (falling back to `src_path`), `Path.resolve()`, and
  require:
  - the row exists and belongs to a title (`404 MEDIA_FILE_NOT_FOUND` otherwise);
  - the path exists and is a file (`404 MEDIA_FILE_MISSING`);
  - the path is inside one of `movies_dir`, `tv_dir`, `anime_dir` after resolution
    (`403 PATH_OUTSIDE_LIBRARY`);
  - `mpv_enabled` (`409 MPV_DISABLED`);
  - `shutil.which(mpv_bin)` resolves (`503 MPV_UNAVAILABLE`).
- Spawn `asyncio.create_subprocess_exec(binary, "--no-terminal", str(path))`. No shell, no
  user-supplied flags, `cwd` unset, stdout/stderr pipes.
- **Kill and relaunch**: if a previous launch is still running, terminate *only the child this
  process started* (tracked pid/transport), wait up to 2s, then spawn the new one. Never
  `pkill`, never match on a process name.
- **Spawn and report**: do not wait for a window. Watch the child for 2 seconds; a non-zero
  exit inside that window returns `502 MPV_FAILED` with the first stderr line (truncated to
  300 chars, path stripped) so "no display" and "bad file" are visible instead of silent.
- Double clicks within 2s are de-duplicated and return the same response.
- Success: `202 {"media_file_id": "...", "launched": true}`.
- Log the media-file id and the exit code only. Never log the path, never log argv.

### Doctor

- New `mpv` line: `ok`, `missing` (`which` failed), `no-display` (neither `DISPLAY` nor
  `WAYLAND_DISPLAY` in the service environment) or `disabled`.
- Checking the doctor must never launch mpv.

### UI

- `MediaFileCard` (carousel and files page) gains the click behaviour:
  - `mpv_enabled` on → the card is a `<button>`; click launches, shows a busy state on that
    card, and fires a toast on failure with the server message.
  - `mpv_enabled` off → the card is the existing link to the title page.
  - One card busy at a time; a second click while pending is ignored.
- **Scroll guard**: record pointer-down coordinates, ignore the click if the pointer moved
  more than 8px or ended on a different element. Carousel drags and touch scrolls therefore
  never launch a player. Keyboard Enter/Space always activates.
- The control is rendered whenever the flag is on, even if the doctor says `missing`; the
  click then reports `MPV_UNAVAILABLE` as a toast with the reason.
- No mpv affordance appears in the library grid or on download rows.
- Remove any Jellyfin play link if one exists (none is added by T-047).
- `AGENTS.md` §1 Playback row and the "Daku does not play video itself" line are updated in
  the same change.

## Error codes

| Code | Status | When |
|---|---|---|
| `MEDIA_FILE_NOT_FOUND` | 404 | Unknown id |
| `MEDIA_FILE_MISSING` | 404 | Row exists, file does not |
| `PATH_OUTSIDE_LIBRARY` | 403 | Resolved path escapes a library root |
| `MPV_DISABLED` | 409 | Setting is off |
| `MPV_UNAVAILABLE` | 503 | Binary not found |
| `MPV_FAILED` | 502 | Child exited non-zero within 2s |

## Out of scope

Resume position, playlist/queueing, subtitles selection, a remote mpv over IPC, launching on
the *client* machine, Jellyfin playback links, a player in the browser.

## Acceptance criteria

- [ ] With `mpv_enabled` on, clicking a file card launches mpv with the resolved library path
      and returns `202`.
- [ ] With the flag off, the card navigates to the title page and no launch endpoint is called.
- [ ] An id for a missing file returns `404`; a path outside the library roots returns `403`.
      A hand-crafted request carrying a path or extra argv is ignored entirely.
- [ ] Clicking a second file while mpv runs kills only Daku's own child and launches the new
      file; a user-started mpv is untouched.
- [ ] If mpv cannot open a window, the card shows a toast with the server's message within
      ~2 seconds instead of failing silently.
- [ ] Dragging the carousel or scrolling the files page never launches a player.
- [ ] Doctor reports `ok` / `missing` / `no-display` / `disabled` without spawning mpv.
- [ ] AGENTS.md §1 records the amendment.
- [ ] No file path, argv or magnet appears in logs.

## Test plan

- `tests/routers/test_play.py`: happy path with a stub child, disabled flag, missing file,
  path traversal, extra body ignored, kill-and-relaunch, failure within 2s, de-duplication.
- `tests/services/test_mpv.py`: `which` resolution, argv construction, stderr truncation and
  path stripping, termination of the tracked child only.
- `tests/test_doctor.py`: the four mpv states, no launch.
- `web/src/components/__tests__/media-file-card.test.tsx`: link vs button by flag, scroll
  guard, busy state, error toast.
- `web/src/pages/__tests__/settings.test.tsx`: toggle and binary path round-trip.

## Implementation notes

- Hold the tracked child in `app.state.mpv_process`; clear it on exit so a stale pid is never
  signalled after reuse.
- `start_new_session=True` keeps mpv out of Daku's process group so a unit restart does not
  kill the player; the endpoint still tracks and terminates only its own child.
- The unit file may need `Environment=DISPLAY=:0` on headless-ish sessions; document it in
  README rather than guessing a display in code.
- Keep the whole feature behind one module (`services/player/mpv.py`) so replacing it with a
  socket-based player later does not touch the router.
