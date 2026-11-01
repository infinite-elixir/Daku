# T-054 — Playback progress and a play affordance

**Phase:** 13 · **Depends on:** T-048 · **Estimate:** M

## Goal

Show how far the host player got through a downloaded file, and make the play action
obvious when a file card is hovered.

Daku still decodes nothing. mpv writes a JSON IPC socket (`--input-ipc-server`); Daku only
reads `time-pos` and `duration` from it. There is no seek, pause or stop command, and no
in-app `<video>`.

## Scope

- `services/player/mpv.py`: create `data_dir/mpv/mpv.sock`, observe `time-pos` and
  `duration`, expose `current_progress()`, persist through a sink every five seconds and
  once on exit. A missing socket, a broken line or a failing sink never fails playback.
- `media_files` gains `playback_position_s`, `playback_duration_s`, `playback_updated_at`
  (migration v13). Duration falls back to the ffprobe `duration_s`.
- `GET /api/media-files/playback` returns the tracked child's live position or `null`.
- `GET /api/titles/{id}/files` items carry a `playback` object.
- `MediaFileCard`: a thin accent seek bar over the artwork (live value wins over the stored
  one) and a play badge that appears on hover/focus while the player is enabled.

## Out of scope

Resuming from the stored position, pause/seek control, marking titles watched, progress for
files played outside Daku.

## Acceptance criteria

- [x] Launching a file records a position at least every five seconds and again when mpv
      exits; the value survives a restart.
- [x] A card shows a seek bar proportional to `position_s / duration_s`, preferring the live
      value over the stored one, and no bar when no position is known.
- [x] Hovering or keyboard-focusing a card shows a play badge when `mpv_enabled` is on and
      shows nothing when it is off.
- [x] A missing or broken IPC socket never fails the launch or the request.
- [x] No path, argv or magnet is logged; the socket path is only used locally.
- [x] `scripts/check.sh` and `pnpm -C web test` pass.

## Implementation notes

- The socket lives at `data_dir/mpv/mpv.sock` (directory mode `0700`), is unlinked before
  each launch and removed on shutdown. Only one child is ever tracked, which T-048 already
  serialises.
- `useLivePlayback` polls `/api/media-files/playback` every three seconds, and only while
  the reader is enabled, so isolated component tests never issue a request.
- Progress is display-only metadata, not a state-machine state.
