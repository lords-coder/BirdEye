# Hardware Acceptance Runbook (07) — two physical cameras

Status: **PENDING** — run on the deployment host, on the camera LAN, with
Redis + API + Celery worker + GPU inference up (native stack recipe in the
readiness report). Nothing below is pre-claimed.

## The pair under test

| # | purpose | host | username | role | pipeline type |
|---|---|---|---|---|---|
| 1 | Classroom exterior / doorway | 10.10.150.178 | user | `doorway_exterior` | exit |
| 2 | Classroom interior | 10.10.149.188 | user1 | `classroom_interior` | entry |

Pre-check (2026-10-15, dev machine): both hosts answer TCP on 554.
Passwords are **not** in this repo, any report, or any fixture — enter
them only in the camera form / edit dialog (stored Fernet-encrypted).
Security note: the stream password was shared in a chat message during
this phase; if that channel is logged or shared, **rotate the camera
passwords** after acceptance and update the stored credentials via the UI.

## Step 1 — register (admin → Cameras → Add camera)

Per camera: Name (`Classroom Exterior` / `Classroom Interior`), Room
(`Classroom`), Camera role (doorway/interior), Pipeline type (exit/entry),
Host, Port `554`, Username, Password, Stream path (see step 2). Save
offline hardware as disabled — the system must never show it operational.

## Step 2 — discover the stream path

Vendors differ (`/Streaming/Channels/101`, `/h264`, `/live`, …). With the
password filled, use **Test connection** (ad-hoc pre-save, or per-camera
after save). Expected progression: `timeout/unreachable` (address wrong) →
`error: verify username, password and stream path` (path wrong) →
`connected + latency` (correct). Record the working path per camera in the
camera Notes field. If the vendor needs `rtsps://` or a non-554 port,
adjust the same fields — no code changes.

## Step 3 — live preview

Camera row → **preview**: interior view should show the classroom;
exterior the doorway. Snapshots poll ~2.5 s with the admin token header
(no credentials in the browser).

## Step 4 — pipeline run (one camera at a time first)

Start the interior camera, walk in with an enrolled student, check Live +
History: one interval opens. Walk out past the doorway camera (started
second): the interval closes with the observed exit time. Then run both
together and repeat. Watch for `NEEDS_REVIEW` events (exit without entry)
in Attendance → presence review; resolve or dismiss each.

## Step 5 — period attendance

With a period covering the lecture (e.g. 09:30–10:30, required 30 min),
open Periods → Compute: presence ≈ time inside, machine PRESENT/ABSENT as
appropriate; apply one test override with a reason and confirm the audit
history row, then leave or reverse it per policy.

## Step 6 — teacher sign-off

Log in as the assigned teacher: My classes shows only their course(s);
override one record with a reason; confirm a second teacher (or logged-out
URL manipulation) cannot reach it (403).

## Known limitations (do not accept against these)

- **Direction** is inferred from placement (interior = entered,
  doorway-after-inside = left), not from video motion vectors. A student
  seen only on the exterior camera is flagged for review, not auto-exited.
- Single-frame snapshots/MJPEG preview are admin spot-checks, not
  monitoring at scale. The pipeline + WebSocket feed is the live path and
  needs Redis running — without it the socket reports "event bus
  unavailable" instead of dying (fixed this phase), but no events flow.
- Real-camera TAR/FAR still requires `check_accuracy.py` with the enrolled
  set; synthetic tests claim nothing about it.

## Acceptance record

| check | result | by / at |
|---|---|---|
| exterior test-connection `connected` | PENDING | |
| interior test-connection `connected` | PENDING | |
| entry opens interval (interior) | PENDING | |
| exit closes interval (doorway) | PENDING | |
| period PRESENT/ABSENT correct | PENDING | |
| override + audit correct | PENDING | |
| teacher isolation holds | PENDING | |
| passwords rotated after chat exposure | PENDING | |
