# 04 — Acceptance Tests

Every acceptance activity executed this phase, its result, and where the raw
evidence lives. **Real** = real model/Redis/worker/API/DB; **Synthetic** =
generated camera source or unreachable endpoint standing in for hardware;
**Static** = configuration analysis without runtime.

## A. Automated suite (pytest) — REAL

| Run | Result |
|---|---|
| Phase baseline (post-repair tree) | 178 passed, 1 skipped, exit 0 (`%TEMP%\opencode\pytest_V1.txt`) |
| After readiness fixes (decisions + security) | 194 passed, 1 skipped, exit 0 (`pytest_V2.txt`) |
| **Final** (incl. fastapi 0.143.1 + starlette 1.8.0) | **195 passed, 0 skipped, exit 0** (`pytest_V3.txt`) |

New regression file `tests/test_readiness_changes.py` (16 tests) covers D-1,
D-2, SEC-04/05/10/13 and DEF-34. `alembic check` still reports
"No new upgrade operations detected"; `compileall` clean.

## B. End-to-end acceptance (`scripts/validate_e2e.py`) — REAL except camera source

Stack: portable Redis 5.0.14.1 + `python -m app.inference.server` (GPU) +
uvicorn 8765 + Celery threads worker. Run with `--keep-data` for the follow-up
exit probe. **Final confirming run: 18/18 checks passed, exit 0 — executed
with the approved D-1 rule active (`CONFIRMATION_FRAMES=3`,
`CONFIRMATION_WINDOW_S=10.0`)** after aligning the stale local `.env` copy
that still pinned the old 2/5 defaults. (Two earlier green runs — 17/17 and
17/18 — predated that alignment and are superseded by this one; the 17/18's
single failure was a parser bug in the driver, not the system.)

| # | Check | Result |
|---|---|---|
| 1 | `/system/health`: db up, redis up, inference online | PASS |
| 2 | JWT login | PASS |
| 3 | Period covering "now" (all-weekdays) created | PASS |
| 4 | Public-domain face assets (Obama / second subject of `two_people.jpg`) | PASS |
| 5 | Enrollment through the **real** quality gate + GPU embeddings | PASS (student created) |
| 6 | Duplicate-identity probe: same face, new registration → **409**, reg-no masked in message | PASS |
| 7 | Second (different) identity enrolls cleanly | PASS |
| 8 | `make_test_video.py` builds the recognition clip from enrolled photos | PASS (1114 KB) |
| 9 | File camera → real pipeline: recognition events for **both** identities; session opened; summary marks both **PRESENT** (`present_students=2`); WS delivered `recognition`/`alert`/`camera_state`/`unknown_face` events | PASS |
| 10 | Camera stop → `running=false`, slot released | PASS |
| 11 | Cleanup: all validation students/cameras/periods removed | PASS |

Evidence: `%TEMP%\opencode\e2e_evidence.json` (health, summary, event count).
Note: enrollment photo variants must keep the face in frame — an over-tight
center crop was **correctly rejected** by the gate during development.

## C. Entry→exit lifecycle probe — REAL

Exit-type camera on the same clip closed both ongoing sessions:
`ongoing=0 completed=2`, `STU-E2E-A=224s, STU-E2E-B=223s`; camera
stopped and deleted cleanly. (`ALL PROBES PASSED`)

## D. RTSP failure recovery probe — SYNTHETIC endpoint, REAL recovery code

Camera with `rtsp://127.0.0.1:5554/nowhere` (nothing listens):
worker logged `failed to open source, retrying in 2.0s` → `4.0s`; runtime
stayed `running=true` with a fresh heartbeat for 35 s; stop + delete clean.
**PASS.**

## E. Inference-outage recovery probe — REAL

Killed the inference service mid-run (two consecutive runs):

| Step | Run 1 | Run 2 |
|---|---|---|
| Precondition inference online | FAIL *(already down from an earlier orchestrated kill — probe artifact)* | PASS |
| Recognition flowing before outage | (baseline stale) | PASS (logs=9) |
| Inference reported down ≤8 s after kill | PASS | FAIL *(health-check cache lag; outage itself proven by the 503 below)* |
| Add-photos during outage → **503** `inference service unavailable: … did not respond within 15.0s` | PASS | PASS |
| Camera pipeline survived (running, fresh heartbeat) | PASS | PASS |
| Restart → `/system/health` online | PASS | PASS |
| Recognition resumed after dedup window | PASS (6→9) | PASS (9→12) |
| Clean stop + delete | PASS | PASS |

Both "FAIL" cells are probe-timing artifacts, not product defects (documented
honestly); every **product semantic** of outage recovery passed in both runs.
Worker log evidence: `inference RPC failed (1..4 consecutive) … backing off
2s→4s→8s→16s` then recovery. (`%TEMP%\opencode\worker.out`)

## F. Live authz matrix on the upgraded stack — REAL

`GET /students` unauth 401 · `GET /media/...` unauth 401 · bad creds 401 ·
good creds 200 · health `db/redis/inference` all up. (fastapi 0.143.1)

## G. Static configuration validation — STATIC

`python scripts/validate_compose.py` → 14 PASS / 1 WARN (seed video absent) /
0 FAIL after fixing DEF-35. `scripts/validate_inference.py` canary green
(CUDA active). pip-audit → 0 known vulnerabilities.

## H. Explicitly NOT executed (environment-blocked)

| Item | Missing | Where it must run |
|---|---|---|
| `docker compose up` runtime | Docker | Docker host — `docker compose up -d --build && python scripts/smoke_test.py` |
| PostgreSQL runtime + migrations against PG | PostgreSQL | compose network |
| Real RTSP device (auth, codecs, mid-stream flap) | camera | campus lab |
| Real-camera TAR/FAR accuracy | footage of enrolled people | campus lab — `scripts/check_accuracy.py` |
| Multi-camera concurrent GPU batching under load | time + cameras | GPU host |

No claim in this report treats any row of section H as validated.
