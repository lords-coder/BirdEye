# Period Attendance Engine (03)

## Policy (`app/services/attendance_engine.py`)

- Window: `[start_time, end_time)` on the local date (`Asia/Kolkata`
  production; tests run UTC), converted to UTC for comparison.
- Every confirmed interval of the student that day is **clipped** to the
  window (pre/post-lecture time never counts) and merged before summing —
  overlaps cannot double-count.
- Threshold: `max(required_minutes, required_ratio × length)`, per-period
  values winning over globals. Default (D-7): **30 min of a 60-min lecture**
  (`ATTENDANCE_DEFAULT_REQUIRED_MINUTES=30`, ratio mode off).
- Statuses: `SCHEDULED` (before start), `IN_PROGRESS` (running —
  provisional, never ABSENT mid-lecture), `PRESENT`/`ABSENT` (after close),
  `NEEDS_REVIEW` (open review evidence that could flip a non-present
  result; clear above-threshold evidence still yields PRESENT).
- Open (unresolved) sessions count up to *now*, capped at period end; the
  missing exit is never invented.

## Records & overrides

- `attendance_records` (unique per period/student/date): `presence_seconds`,
  `threshold_seconds`, `machine_status` (never destroyed), `effective_status`.
- `GET /attendance/period/{id}/records?date=` recomputes idempotently and
  returns the table (window, threshold, running/finished flags, intervals).
- `POST /attendance/records/{id}/override` `{status, reason}`: explicit
  PRESENT/ABSENT + non-blank reason (server minimum
  `OVERRIDE_REASON_MIN_LENGTH=5`, 422 otherwise). Machine result preserved;
  recomputation never silently clears an override.
- `GET /attendance/records/{id}/overrides`: full append-only history
  (previous/new, reason, actor identity + role, timestamp). Corrections by
  other authorized users append; history only grows.

## Boundary evidence (tests)

29:59 → ABSENT vs 30:00 → PRESENT on a 60-min lecture; full-window clip of
a 08:00–12:00 session → exactly 3600 s; outside-window session → 0 s;
unresolved session counts without fabricating an exit.
