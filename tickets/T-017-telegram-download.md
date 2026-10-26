# T-017 — Download from Telegram

**Phase:** 4 · **Depends on:** T-010, T-013, T-016 · **Estimate:** M

## Goal

Take a linked channel message and materialise it as a local file, then run it through the
normal inspect + organise pipeline.

Two modes:

- **Local Bot API server** (`--local`): `getFile` returns an absolute path; Daku copies it.
  No size cap.
- **Cloud API**: `getFile` works only up to 20 MB; larger files are refused with guidance.

## Scope

- `POST /api/downloads {telegram_message_id}` creating `downloads` with `source="telegram"`,
  `engine="local"`, `external_id=<message_id>`.
- `services/engines/local_copy.py`: copy with progress, chunked, `asyncio.to_thread`.
- Cloud fallback: download the `file_path` URL via aria2 when `file_size <= 20 MB`.
- Worker integration with the existing state machine.
- UI button "Download from Telegram" on title/episode and in the inbox.
- Doctor: confirms local mode and that `getFile` returns a readable absolute path.

## Out of scope

Streaming directly from Telegram, deleting channel messages, MTProto.

## Acceptance criteria

- [ ] In local mode, a 700 MB video downloads to `downloads_dir` with progress visible and
      without a 20 MB error.
- [ ] In cloud mode, a file ≤ 20 MB downloads successfully; a larger file fails immediately
      with code `TELEGRAM_SIZE_LIMIT` and a message offering the two real options: enable the
      local Bot API server, or upload the file directly from the phone's browser.
- [ ] The source channel message is never modified or deleted.
- [ ] The copied file is byte-identical (size + sha256 optional) to the Telegram original.
- [ ] Interrupting and retrying resumes from a partial file (`.part`) rather than restarting.
- [ ] The download enters `complete → organizing → available` and appears in Jellyfin like a
      torrent download.
- [ ] Two concurrent downloads of the same message are rejected by
      `UNIQUE(engine, external_id)`.
- [ ] A missing local `file_path` (server restarted, cache cleared) re-requests `getFile` once,
      then fails with `TELEGRAM_FILE_EXPIRED`.

## Implementation notes

Local mode: `getFile(file_id)` → `result.file_path` is an absolute path on the Bot API host.
Require that Daku and `telegram-bot-api` run on the same machine and share a filesystem
namespace; otherwise the path is unusable. If the path is relative, resolve it against the
server's `--dir`. Verify with `os.path.isfile` before copying, and budget 2× the file size in
free space (bot server cache + Daku copy).

Cloud mode: `getFile` → `https://api.telegram.org/file/bot<token>/<file_path>`; hand that URL
to aria2 with `dir=downloads_dir`. Never log the URL (it contains the token).

Progress: copy in 4 MB chunks, update `downloaded_bytes` and publish events at most 1×/s.

Cleanup: on success remove the `.part`; on failure keep it for resume unless the user cancels.

Schema note: `downloads.engine` gains the value `local`. Update `AGENTS.md` §5 in the same PR.

## Test plan

- `tests/engines/test_local_copy.py`: copy, resume, missing source, permission error, hash.
- `tests/telegram/test_download.py`: local mode, cloud under/over 20 MB, expired file,
  duplicate request, channel message untouched.
