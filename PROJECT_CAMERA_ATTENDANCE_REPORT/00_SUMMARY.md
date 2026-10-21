# Camera Attendance Phase — Summary (00)

Date: 2026-10-15. Scope: camera management, live entry/exit tracking,
period-based attendance engine, role-based teacher portal — built on top of
the repaired system without rebuilding it. Baseline entering this phase:
195 passed. Baseline leaving it: **223 passed (195 + 28 new), 0 failures**.

## What was built

1. **Camera management** — structured connection fields (host/port/username/
   Fernet-encrypted password/stream path), physical role
   (`classroom_interior` / `doorway_exterior`), room, notes, enable/disable
   flag, health telemetry, classified connection testing
   (connected/auth_failed/unreachable/timeout/no_frames/unsupported/error/
   disabled), authenticated snapshot + MJPEG preview. Passwords are
   write-only and never leave the server (`01_CAMERA_MANAGEMENT.md`).
2. **Entry/exit tracking** — persistent per-student state machine
   (OUTSIDE/ENTRY_PENDING/INSIDE/EXIT_PENDING/NEEDS_REVIEW) with an
   append-only audit trail, layered on the unchanged session engine. Exit
   sightings without an active session are flagged for review, never
   invented (`02_ENTRY_EXIT_TRACKING.md`).
3. **Attendance engine** — per-period records from presence clipped to the
   scheduled window, default policy 30 min of a 60-min lecture, machine vs
   effective status, audited mandatory-reason overrides
   (`03_ATTENDANCE_ENGINE.md`).
4. **Teacher portal** — `teacher` role, courses with teacher assignment,
   course-scoped periods/records/overrides/history, role-based UI landing
   (`04_TEACHER_PORTAL.md`).
5. **Socket fix** — `/ws/live-feed` no longer drops when Redis is
   unavailable; it stays open with status notices and re-subscribes
   (proven by probe). Teacher tokens get a clean 4401 with no retry storm.

## Verification (evidence, not claims)

- `pytest`: 223 passed, 1 warning (pre-existing httpx deprecation).
- `alembic check`: clean; migration 0006 round-trips up/down/up on SQLite.
- Browser-verified against a live server: login/role nav, camera CRUD +
  health, period records table, override dialog incl. blank-reason
  rejection, history filters, teacher landing + scoping.
- Camera reachability: both acceptance cameras answer TCP on 554 from the
  dev machine; stream test without password honestly reports "verify
  username, password and RTSP stream path". **No authenticated stream test
  has been performed yet** — the password was never handled by the
  implementer and no stream accuracy is claimed (`07_HARDWARE_RUNBOOK.md`).

## Decisions added (D-7…D-11)

- **D-7:** default attendance policy = 30 min within a 60-min lecture
  (`ATTENDANCE_DEFAULT_REQUIRED_MINUTES=30`), per-period overridable.
- **D-8:** live socket keeps the connection across Redis outages with
  `status` notices + backoff re-subscribe; 4401 ends the session.
- **D-9:** `PRESENCE_TRANSITION_OBSERVATIONS=1` default (the K-of-N
  confirmed sighting completes the transition; raisable per placement).
- **D-10:** `/system/health` stays unauthenticated (load-balancer
  convention); verified to carry no PII/secrets.
- **D-11:** for legacy `both` cameras, exit evidence wins while INSIDE
  (exit-when-open preserved from the original session engine).

## Bugs found by the new tests (all fixed)

- Race in presence-state creation under concurrent observers
  (IntegrityError → now catch-and-reuse).
- Pending-transition counters reset by the transition helper (off-by-one
  that froze ENTRY/EXIT_PENDING at level 2).
- `/period/{id}/records` shadowed by `/period/{id}/{student_id}`
  (registration order; literal routes now registered first).
- Teacher-tab reconnect storm on 4401 (client now stops retrying).

## Remaining work

Hardware acceptance (password entry, stream paths, placement, direction
limits) per `07_HARDWARE_RUNBOOK.md`; open items in `08_REMAINING_ISSUES.md`.
No production-readiness claim is made until the acceptance run passes.
