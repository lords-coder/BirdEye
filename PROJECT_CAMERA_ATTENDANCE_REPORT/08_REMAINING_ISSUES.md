# Remaining Issues (08)

Carried owner items O-1…O-5 still await institutional sign-off
(confirm.py/formcsv.py/evalmetrics.py fate; threshold re-calibration;
alert values; reset-cap policy; full authz/role-model review — the teacher
role added this phase partially addresses the last).

## New / updated open items

- **H-1 (new): RTSP stream paths unknown.** Both acceptance cameras answer
  on 554; the per-model path is discovered in runbook step 2. No path is
  assumed anywhere in code.
- **H-2 (new): stream passwords pending.** Metadata registered during
  verification, then removed with all dev fixtures. Passwords enter only
  via the UI on the deployment host. Rotate them post-acceptance (chat
  exposure note in `07`).
- **H-3 (new): Redis is required for the live path.** The socket now
  degrades gracefully, but events only flow with the bus up. The portable
  Redis recipe remains manual.
- **H-4 (new): direction detection is placement-inferred** (documented in
  `02` and `07`); true motion-vector direction is out of scope.
- **H-5 (new): preview scaling.** On-demand snapshot/MJPEG opens the
  source per request; concurrent viewers multiply load. Prefer snapshots;
  monitor camera CPU during acceptance.
- **H-6 (new): `?token=` preview/WS auth** trades URL exposure for
  `<img>`/EventSource compatibility (same as the pre-existing WS pattern);
  snapshots use the header. Documented, not yet moved to subprotocols (P2).
- Prior constraints stand: no Docker/WSL on this machine (static compose
  validation only, 14 PASS/1 WARN from readiness), no PostgreSQL runtime
  (SQLite-verified + Postgres-compatible migrations), no git on machine
  (manifest CSV is the change record).
