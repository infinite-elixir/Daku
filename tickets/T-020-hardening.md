# T-020 — Hardening: retries, disk guard, backups, activity log

**Phase:** 5 · **Depends on:** all · **Estimate:** M

## Goal

Make Daku survive a flaky network, a full disk, a crashed process and a bad API day without
losing state or confusing the user.

## Scope

- `errors.py`: `AppError(code, message, retryable, hint)`; every router error goes through it
  with a stable machine code.
- Retry policy: jittered exponential backoff for metadata, Torrentio, qBittorrent, aria2,
  Jellyfin, Telegram; a per-integration circuit breaker (open after 5 failures for 60 s).
- Disk guard: refuse new downloads below `min_free_gb` (default 10); warn at 20; show free
  space per library root.
- DB backups: daily `VACUUM INTO` copy to `data_dir/backups`, keep 7; restore documented.
- Activity log: every state transition and integration failure visible in
  `GET /api/system/activity` and a "Activity" settings tab.
- Startup recovery: downloads stuck in `organizing` are retried; `active` rows whose external
  job vanished become `failed` with retry; orphan `.part` files are reported, not deleted.
- Log rotation: 10 MB × 5 files; redaction tests for tokens, magnets and API keys.
- Orphan cleanup: DB rows whose files disappeared are marked `missing`, never auto-deleted.

## Out of scope

Metrics/Prometheus, Sentry, email alerts.

## Acceptance criteria

- [ ] Every API error returns `{code, message, hint?, retryable}` and the UI renders the hint.
- [ ] A 500 → 200 recovery is retried with backoff; a permanently failing endpoint trips the
      breaker and the doctor shows `circuit_open`.
- [ ] Starting a download with less than `min_free_gb` free is refused with
      `DISK_LOW` and the required/available figures.
- [ ] A daily backup exists and `sqlite3 backup.db "PRAGMA integrity_check"` returns `ok`.
- [ ] Killing the process mid-organise and restarting resumes or safely retries without
      duplicate library files.
- [ ] Killing qBittorrent mid-download and restarting it recovers tracking without duplicate
      rows.
- [ ] Tokens, magnets, bot URLs and API keys never appear in `daku.log` or activity payloads
      (covered by a redaction test over a corpus).
- [ ] Deleting a file from disk marks the row `missing` and the UI offers "re-download",
      never a crash.
- [ ] `GET /api/system/activity?limit=100` is paginated and filterable by level.

## Implementation notes

Circuit breaker: in-memory per integration; state does not need to survive restart.

Backup: use `VACUUM INTO ?` with a timestamped path; do not copy the live WAL files.

Redaction: a single `redact()` used by the log formatter and the activity writer, with a
regex for `bot\d+:[A-Za-z0-9_-]+`, `magnet:\?[^ ]+`, `api_key=[^& ]+`, and any configured
secret value (exact-match replacement).

Startup recovery runs before the worker loop and must be idempotent.

## Test plan

- `tests/test_errors.py`: shape, codes, retryable flag, UI hint presence.
- `tests/test_retry.py`: backoff timing, breaker open/close, jitter bounds.
- `tests/test_disk_guard.py`: refuse/warn thresholds, per-root reporting.
- `tests/test_backup.py`: create, rotate to 7, integrity check.
- `tests/test_redaction.py`: corpus of secrets in logs and activity payloads.
- `tests/test_recovery.py`: stuck organizing, vanished external job, missing file.
