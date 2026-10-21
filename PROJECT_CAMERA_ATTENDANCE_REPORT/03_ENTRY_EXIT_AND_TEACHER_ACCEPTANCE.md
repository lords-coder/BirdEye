# Entry/Exit & Teacher Acceptance (03)

## Pipeline proof (synthetic video, REAL stack — 18/18 E2E)

File-source camera through GPU inference + Celery worker + Redis + API:
recognition logs for both validation identities, 1 ongoing session (no
duplicates from repeated frames), summary PRESENT for both, **9 WS events
(`alert`, `camera_state`, `presence_state`, `recognition`, `unknown_face`)**,
clean stop. Restart persistence: presence/sessions live in the DB, not in
worker memory (design; worker restart re-reads state).

## Attendance correctness (regression suite, hardware-independent)

32-min vs 24-min accumulation, multi-interval merge without duplication,
outside-window exclusion, in-progress provisional status, unresolved-exit
handling (counts to now, exit never invented), machine/effective
separation, override audit (actor, timestamp, reason, old/new), blank
reason rejected, cross-teacher 403 by URL/body/id manipulation —
all covered in `tests/test_camera_attendance.py` (29 tests) and passing.

## Physical walk-through scenarios: NOT RUN (blocked on stream)

Entry, exit, doorway pass-through, both-camera visibility, lingering,
leave-and-return, overlapping observations, mid-session disconnect —
procedure exists in the prior runbook (`07_HARDWARE_RUNBOOK.md`,
preserved); none executed without authenticated video. No timestamps are
fabricated by the engine for uncertain events (design + tests); worker
uses frame timestamps where the pipeline provides them.

## Legend for this report set

- **Synthetic-passed**: pytest suite, E2E file-camera run, accuracy
  gallery, browser UI checks.
- **Physical-passed**: TCP/RTSP pre-auth interrogation (01) — service
  alive + auth demanded. Nothing further.
- **Blocked**: authenticated stream (user password step), classroom
  walk-throughs, in-situ accuracy, Postgres/Docker runtime.
- **Not run**: walk-throughs, restart-during-session drill on hardware,
  multi-day stale-session observation.
- **Unknown**: real TAR/FAR, production latency over RTSP.
