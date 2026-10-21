# API & Database Changes (05)

## New endpoints

- Courses: `POST/GET /courses`, `GET/PUT/DELETE /courses/{id}`,
  `GET /courses/{id}/periods` (teacher-scoped reads, admin writes).
- Cameras: `POST /cameras/test-connection`, `POST /cameras/{id}/test`,
  `GET /cameras/{id}/snapshot`, `GET /cameras/{id}/stream.mjpeg`;
  create/update extended (structured fields, write-only password).
- Attendance: `GET /attendance/history`, `GET+POST
  /attendance/period/{id}/records|compute`, `POST
  /attendance/records/{id}/override`, `GET
  /attendance/records/{id}/overrides`, `GET /attendance/presence/states`,
  `GET /attendance/presence/events`, `POST
  /attendance/presence/events/{id}/review`.
- Periods: create/update accept `course_id`, `required_minutes`,
  `required_ratio`, `clear_course`; outputs include course/teacher context.
- Route-ordering fix: literal `/period/{id}/records` registered before
  `/period/{id}/{student_id}` (the latter swallowed "records" as a
  student id — caught in browser testing, fixed).

## Migration 0006 (`0006_camera_attendance`, revises 0005)

Cameras: 13 columns + 2 checks. Users: role check +`teacher`. New tables:
`courses`, `presence_states`, `presence_events`, `attendance_records`
(unique period/student/date), `attendance_overrides` (reason non-blank
check). Periods: `course_id` FK + policy columns/checks. Sessions:
`source`/`review_status` + checks. Verified: upgrade→downgrade→upgrade on
SQLite, `alembic check` clean (index names aligned after one drift catch).

## Configuration (`.env.example`)

`ATTENDANCE_DEFAULT_REQUIRED_MINUTES=30`,
`ATTENDANCE_DEFAULT_REQUIRED_RATIO=0.0`, `OVERRIDE_REASON_MIN_LENGTH=5`,
`PRESENCE_TRACKING_ENABLED=true`, `PRESENCE_TRANSITION_OBSERVATIONS=1`,
`PRESENCE_REVIEW_DEDUP_S=300`, `CAMERA_CONNECT_TIMEOUT_S=8`,
`CAMERA_SNAPSHOT_INTERVAL_S=1.5`, `CAMERA_STREAM_FPS=6`.

## WebSocket resilience (`app/api/ws.py`)

Subscribe loop with backoff survives Redis restarts; `status` notices for
degraded/recovered bus; receiver task answers app pings; 4401 for
non-admins. Client: 25 s keepalive, 4401 stops retrying with an explanatory
status, `status`/`pong` frames update the status line instead of the event
list. Root cause of the reported drops: server died on Redis-unreachable
subscribe (plus 4401-retry storm from teacher sessions).
