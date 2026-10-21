# Test Results (06)

Command: `.venv\Scripts\python.exe -m pytest` (never `-q`).
Result: **223 passed, 1 warning, exit 0** (≈40 s). Baseline entering the
phase: 195 passed. Delta: +28 in `tests/test_camera_attendance.py`, zero
regressions in existing files.

## New coverage (28 tests, all hardware-independent)

- Authz: teacher blocked from cameras/students/periods-list/presence/super
  surfaces (+ public `/health` carries no PII); own-courses-only listing;
  cross-teacher 403 on periods/records/override; teacher override allowed
  on own class with actor recorded.
- Secrets: password absent from create/get/list payloads; inline-credential
  URL migrated to encrypted fields with redacted outputs; update requires
  username; clear works; server-side URL rebuild verified.
- Connection: classification matrix (unreachable/timeout via mocked
  probes), `redact()` unit cases, disabled-camera start/test behavior,
  snapshot 403-for-teacher + 502 without credential leak.
- Presence: entry/exit lifecycle with event-time close duration;
  exit-without-session review + dedup + interior resolution; pending levels
  at 2 observations incl. exit-cancel; legacy `both` multi-camera close;
  4-thread concurrent observers → exactly one session.
- Engine: 29:59/30:00 boundary; window clipping; in-progress/scheduled;
  unresolved sessions counted without invented exits; override audit +
  recompute preservation + reversal; NEEDS_REVIEW masking rule.
- History: filters, totals, open-only, teacher section scoping.
- Courses: non-teacher/unknown assignee rejected.
- Socket: teacher 4401; socket survives Redis outage with status notice +
  ping/pong round-trip (fakeredis covers only sync access, so the real
  async bus is genuinely unreachable in tests — a true outage simulation).

## Bugs the suite caught (fixed, none waived)

Presence creation race, pending-counter reset, `both`-camera exit ordering,
`/records` route shadowing (browser), 4401 retry storm (browser).

## Live-verified (browser + API, dev server)

Login/role nav/landing, camera CRUD + classified test + 502 snapshot,
period records table, override dialog incl. blank/short rejection and
audit authoring, history filters/stats, teacher scoping matrix above.
Camera TCP reachability: both acceptance hosts OPEN on 554 from the dev
machine; unauthenticated stream test honestly reports missing credentials.

## Explicitly NOT claimed

No authenticated stream, no recognition accuracy (TAR/FAR), no
multi-person live run — pending hardware acceptance (`07`).
