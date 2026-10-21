# Camera Management (01)

## Data model (`Camera`, migration 0006)

New columns: `host`, `port` (1–65535), `username`, `password_encrypted`
(Fernet via `app.crypto.encrypt_text`, same key discipline as embeddings),
`stream_path`, `camera_role` (`classroom_interior`/`doorway_exterior`),
`room`, `notes`, `enabled` (default true), `last_frame_at`,
`last_test_status`, `last_tested_at`, `last_error` (redacted text only).
Legacy `rtsp_url`/`file_path` rows remain valid.

## Secret handling (audited)

- Password is accepted on create/update, encrypted at rest, **never
  serialized**: responses expose `has_password: bool` and a
  credential-redacted `display_url`.
- Every diagnostic string passes through `redact()` (strips `user:pass@`)
  before responses, DB writes, or logs. Verified by test + browser check.
- Legacy rows with inline-credential `rtsp_url` are migrated on write:
  userinfo moves to structured fields (password encrypted) and the URL
  column is cleared. Until then, all outputs redact it.
- Update semantics: omit `password` to keep, send a value to rotate,
  `clear_password: true` to remove. A password without a username is 422.

## Connection testing

`POST /cameras/test-connection` (stored `camera_id` or ad-hoc fields —
ad-hoc credentials are never persisted) and `POST /cameras/{id}/test`.
Classification: `connected / auth_failed / unreachable / timeout /
no_frames / unsupported / error / disabled`. Strategy: ffmpeg diagnostics
when available (sees RTSP 401s), else TCP pre-probe + OpenCV; when the
transport works but the stream won't open, the result honestly says
"verify username, password and stream path" instead of guessing
auth_failed. Result is stored (`last_test_status/at`, redacted
`last_error`). Offline cameras save as `enabled=false` and are refused a
pipeline start (409) — never reported operational.

## Live preview

- `GET /cameras/{id}/snapshot` — single JPEG, admin only (header or
  `?token=` for `<img>`-style clients, same trade-off as the WS feed).
- `GET /cameras/{id}/stream.mjpeg` — paced multipart preview
  (`CAMERA_STREAM_FPS`). Preview opens the source on demand: fine for
  checks, not a substitute for the pipeline at scale.
- UI polls snapshots with the auth header (no token in URL) every ~2.5 s.

## UI (extends existing design)

Camera table adds Room, Role (interior/doorway badges), Source (redacted),
State, Health (last test + last frame), Enabled; actions: start/stop,
test, preview, edit, enable/disable, delete. Add/edit form carries all
fields with inline test-connection.
