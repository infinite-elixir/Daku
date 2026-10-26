# T-016 — Attach-to-entry and archive channel

**Phase:** 4 · **Depends on:** T-006, T-015 · **Estimate:** L

## Goal

Forward a video to the bot, attach it to a title or episode already in the library, and have
the bot mirror it into the archive channel. Daku keeps the channel `message_id` so it can
fetch the file later.

## Scope

- Handler for `message.video` / `message.document` (video mime types only).
- Caption parsing: `tt1234567`, `tt1234567 S01E02`, or `/link <query>`.
- Inline keyboard of title matches (max 5) + "Search in Daku" web button.
- Callback handling `attach:<short_token>`; short tokens because callback data is limited to
  64 bytes.
- `copyMessage` into `telegram_archive_channel`; store `telegram_messages`
  `(channel_id, message_id, file_id, file_unique_id, title_id, episode_id, state="linked")`.
- Web `GET /attach?t=<signed>`: picker for titles/episodes, same write path.
- `GET /api/telegram/inbox`: unlinked messages; `POST /api/telegram/{id}/attach`;
  `DELETE /api/telegram/{id}` (unlink, does not delete from Telegram).
- Dedupe by `file_unique_id`.

## Out of scope

Downloading the file (T-017), reading channel history (impossible, and forbidden).

## Acceptance criteria

- [ ] Forwarding a 700 MB video to the bot yields a reply with title matches (or a search
      prompt) without downloading the file.
- [ ] Tapping a match copies the message to the archive channel and creates one
      `telegram_messages` row with the channel message id.
- [ ] The same file forwarded twice does not create a second row; the bot says it is already
      linked.
- [ ] A non-video document, a photo, a sticker, or an audio file is rejected with a short
      explanation.
- [ ] A callback token older than 15 minutes is rejected.
- [ ] The web attach link works on a phone on the LAN without logging in, and expires.
- [ ] Unlinking removes the DB row but never deletes the channel post.
- [ ] If `copyMessage` fails (bot not admin, channel wrong), the user gets the exact reason and
      no DB row is written.
- [ ] The inbox lists unlinked files with name, size, date and an Attach action.
- [ ] Channel `message_id` is stored as an integer; the channel id is never inferred from the
      update.

## Implementation notes

Callback data: `attach:<8-byte base64 token>`; keep the mapping in a short-lived in-memory
dict (15 min) plus a DB-backed fallback so a restart does not break open keyboards.

`copyMessage(chat_id=archive, from_chat_id=user_chat, message_id=msg_id)` is a server-side
copy: it works for files of any size and does not hit the 20 MB `getFile` cap. Use it; never
download to attach.

Signed web token: `base64url(msg_row_id.expiry).hmac_sha256(install_secret)`. Verify both
signature and expiry. No auth exists elsewhere, so this token is the only protection — keep it
short-lived.

Title search for the inline keyboard: reuse `GET /api/titles?q=` but cap to 5 and strip to
`{id, label}`. Do not expose poster URLs in callback data.

Caption parsing: accept `tt\d{7,}`, optional `[Ss](\d+)[Ee](\d+)`; if the episode does not
exist, offer to create it from metadata.

## Test plan

- `tests/telegram/test_attach.py`: caption variants, no caption, duplicates, non-video,
  expired callback, copy failure.
- `tests/telegram/test_web_attach.py`: valid token, tampered token, expired token, attach
  writes the same row shape as the bot path.
- `tests/routers/test_telegram_inbox.py`: list, attach, unlink, pagination.
