# Remaining Blockers (06)

1. **Authenticated RTSP stream (both cameras)** — needs the stream
   passwords entered via the UI password field + stream path from vendor
   docs/device web UI. Unblock: 5-min admin procedure in 01. Nothing is
   claimed good from the open port alone.
2. **Classroom walk-through acceptance** — entry/exit/pass-through/
   both-visible/lingering/return/overlap/disconnect scenarios per the
   preserved runbook. Unblock: blocker 1.
3. **In-situ recognition accuracy** — TAR/FAR/latency on enrolled students
   over RTSP under classroom conditions; institutional thresholds
   unsigned (proposals in 02).
4. **PostgreSQL + Docker runtime proof** — no servers on this machine.
5. **Institutional items O-1…O-5** — still awaiting owner sign-off
   (threshold calibration depends partly on blocker 3).
6. **Passwords rotation** — stream passwords were shared in chat during
   the prior phase; rotate after acceptance and re-enter via UI.
7. **Placement caveat (standing)** — direction is placement-inferred, not
   video-measured; if walk-throughs show ambiguity, smallest fix is a
   doorway-mounted `both`-role view covering the threshold line.

Non-blockers: suite 224 green, migrations clean, E2E 18/18, accuracy
gallery clean-margin, compose static 14/1, reports complete.
