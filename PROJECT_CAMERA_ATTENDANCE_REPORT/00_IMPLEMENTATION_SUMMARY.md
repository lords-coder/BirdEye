# BirdEye Acceptance — Implementation Summary (00)

Date: 2026-10-15. This phase validated the camera-attendance build on real
hardware signals and the real native stack. Prior state reconciled first:
`PROJECT_CAMERA_ATTENDANCE_REPORT/` (00–09, preserved), readiness/repair
reports intact, suite re-baselined at 223 passed before any change.

## What this phase did (no rebuilds)

1. **Reconciled state** — suite 223 passed on arrival; camera metadata rows
   re-registered after the prior cleanup (`Classroom Exterior`
   10.10.150.178:554 doorway/exit; `Classroom Interior` 10.10.149.188:554
   interior/entry; no passwords stored).
2. **Real-device interrogation without credentials** — TCP 554 OPEN on both
   hosts; bare RTSP OPTIONS/DESCRIBE answered `401 Unauthorized` with
   Digest auth on both; 8 standard stream paths uniformly 401 (path
   existence not distinguishable pre-auth — path must come from vendor
   docs/device UI, port 80 is open on both for the config interface).
3. **Full native-stack validation** — portable Redis + uvicorn API + threads
   Celery worker + GPU inference (buffalo_l on CUDA, RTX 5060):
   `validate_e2e.py` **18/18 passed** (real enrollment gate, duplicate
   409, file-camera pipeline, sessions, PRESENT summary, 9 live WS events
   incl. `presence_state`); `check_accuracy.py` measured (see 02).
4. **One evidence-based fix** — `Camera.build_rtsp_url()` emitted
   `user:@host` when no password was stored yet; now omits the empty field
   (`user@host`), +1 regression test.
5. **Reports 00–08** (this set); previous 00–09 preserved unmodified.

## Results at a glance

| area | result |
|---|---|
| pytest (full) | **224 passed**, 0 failed (223 + 1 new) |
| alembic check | clean; head `0006_camera_attendance` |
| E2E synthetic video (real stack) | 18/18 passed |
| Accuracy gallery (public figures) | TAR 100% / FAR 0% @0.25–0.55, rank-1 23/23, unknown 8/8 rejected — small gallery, NOT production accuracy |
| compose | 14 pass / 1 pre-existing warn (static only; no Docker host) |
| authenticated RTSP stream | **BLOCKED** — password entry is a user step (procedure in 01) |
| PostgreSQL runtime / Docker runtime | **BLOCKED** — absent on this machine |

## Definition-of-done status

All safe validation executed; the one defect found fixed + regression
tested; acceptance procedure documented; blockers listed in 06. **Not
claimed:** production readiness, real-camera accuracy, authenticated
streaming — each is marked PENDING/BLOCKED with its unblock step. No SRM
official-deployment claim until 07's checklist is fully signed.
