# Security & Reliability Results (05)

Re-verified this phase (suite + live probes; controls preserved, one
minimal fix):

- **Credential encryption/redaction**: password absent from all API
  payloads (byte-checked), Fernet at rest, redacted diagnostics (live
  401-stderr from ffmpeg contained no secret), empty-password URL fix.
- **Role-enforced camera access**: teacher 403 on cameras/students/
  periods-list/presence/super surfaces (tests); snapshot 403 for
  teachers, 502-without-leak for admins.
- **Authenticated stream access**: preview/snapshot endpoints admin-only
  (header or `?token=`); WS admin-only with 4401 + no-retry client.
- **Teacher class authorization**: cross-teacher 403 matrix live-verified
  previously and regression-covered (URL/body/id manipulation).
- **WS reconnection/revocation**: survives Redis outage with status
  notices (test-simulated true outage); 4401 ends sessions; revoked/
  disabled accounts resolve to unauthorized via full-principal check.
- **Login throttling**: carried from hardening phase (not re-drilled
  here); **degraded Redis behavior verified live** — API serves DB-backed
  reads, socket reports bus state instead of dropping.
- **Inference outage/worker recovery**: pipeline per-frame try/except +
  rollback (design); restart-during-session drill on hardware not run.
- **Duplicates/transactions**: K-of-N + dedup windows, merged interval
  math, per-student state lock + creation race fixed this phase (test),
  unique record constraint; no partial-write observed.

No production-secure claim is made from dependency advisories or from
these functional checks alone; institutional review items (O-1…O-5)
remain open.
