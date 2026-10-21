# Remaining Blockers — Live Monitor (14)

## Blocking physical-camera acceptance

1. **No stored stream passwords.** Neither Classroom Interior nor
   Classroom Exterior has a password entered through the secure UI.
   The authenticated RTSP stream has never been opened. The physical
   acceptance procedure (enter password → Test → Preview → walk-through)
   cannot proceed until the user enters both passwords via the Cameras UI.

2. **Stream path unconfirmed.** Both cameras respond `401` to every
   standard RTSP path tested (`/Streaming/Channels/101`, `/h264`,
   `/live`, Dahua/Axis variants). The working path must come from the
   vendor documentation or the camera's own web interface.

## Resolved in this phase

- Missing live video transport (root cause: no pipeline-to-browser path)
- Worker crash on Windows (`ThreadPool does not implement kill_job`)
- No inference-outage visibility (now surfaced in `live-status`)
- No stale-frame guard on camera switch (now via `X-Camera-Id` header)
- No credential leakage in stream bytes (tested and verified)

## Carried forward from earlier phases

- PostgreSQL + Docker runtime proof (absent on this machine)
- In-situ recognition accuracy on physical cameras (requires password entry)
- Institutional sign-off on accuracy thresholds (proposed, not approved)
- O-1…O-5 owner items (threshold calibration, alert values, reset-cap
  policy, authz granularity, confirm.py/formcsv.py/evalmetrics.py fate)