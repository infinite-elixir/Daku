# T-015 — Telegram bot skeleton

**Phase:** 4 · **Depends on:** T-001 · **Estimate:** M

## Goal

A running, locked-down bot that only answers allowlisted users, reports its capabilities, and
tells you whether it is talking to the cloud API or a local Bot API server.

## Scope

- `services/telegram/bot.py`: `python-telegram-bot` application started in the FastAPI
  lifespan; polling mode (no webhook, LAN host).
- Config: `telegram_bot_token`, `telegram_api_base`, `telegram_archive_channel`,
  `telegram_allowlist`.
- Allowlist filter applied to every handler (messages, callbacks, inline queries).
- Commands: `/start`, `/help`, `/id` (returns user id, chat id, and whether allowlisted).
- `getChat` validation of the archive channel at startup (must be numeric `-100...`, bot must
  be an admin there).
- Doctor check: token valid (`getMe`), mode (`cloud` or `local`), archive channel reachable,
  local server version, and the effective download cap.
- Settings UI card showing bot username, mode, channel, and the exact `logOut` command when
  the user wants to switch to a local server.

## Out of scope

Receiving media, attaching to entries (T-016), downloading (T-017).

## Acceptance criteria

- [ ] With a valid token and allowlist, `/start` replies within 2 s.
- [ ] A message from a non-allowlisted user gets no reply and is logged at `warning`
      without the message body.
- [ ] An empty allowlist denies everyone; the doctor says `allowlist_empty`.
- [ ] `/id` shows the numeric user id and chat id so the user can add themselves.
- [ ] Startup with an invalid token does not prevent Daku from serving HTTP; the doctor
      reports the failure and the bot stays disabled.
- [ ] `telegram_api_base` set to `http://127.0.0.1:8081` makes the doctor report `local` and
      "no 20 MB download cap"; otherwise `cloud` and "20 MB cap".
- [ ] In `cloud` mode the doctor/settings card shows the exact `logOut` call and the local
      server command needed to lift the cap, plus the direct-upload fallback link.
- [ ] The bot never logs the token, and exception messages are scrubbed of
      `bot<token>` URL fragments.
- [ ] `telegram_allowlist` is editable from the settings API and takes effect without restart.

## Implementation notes

`python-telegram-bot` v21:
`Application.builder().token(t).base_url(base).base_file_url(...).build()`.
For a local server both `base_url` and `base_file_url` must point at it.

Polling: `drop_pending_updates=True` on startup so a backlog of old messages is not processed.
Run `application.start()` without `updater.start_polling()` blocking the event loop.

Channel validation: `getChat(chat_id)` → `type == "channel"`, then `getChatMember(chat_id, bot.id)`
→ `status in {"administrator", "creator"}` and `can_post_messages`.

Do not add a webhook path in this ticket.

## Test plan

- `tests/telegram/test_filters.py`: allowlist pass/deny, empty list, string vs int ids.
- `tests/telegram/test_startup.py`: invalid token disables bot but not app; local vs cloud
  mode detection; channel validation failure.
- `tests/telegram/test_redaction.py`: token never appears in a logged exception.
