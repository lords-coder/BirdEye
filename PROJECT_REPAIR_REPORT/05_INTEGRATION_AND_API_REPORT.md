# 05 — Integration and API Report

## 1. Live stack boot (this machine)

Started exactly as a user would (`.env` written by `setup_dev.ps1`, DB migrated
to 0005, no Redis/Docker/GPU present):

```
uvicorn app.main:app --host 127.0.0.1 --port 8765     → READY
GET /api → 200 within ~1 s (lifespan: admin seed + SQLite open)
```

Service health from inside: `{'database': 'up', 'redis': 'down',
'inference': 'unknown'}` — i.e. the app reports its own degradation honestly.

## 2. `scripts/smoke_test.py` against the live server (final run, after BUG-33)

```
21 checks: 19 passed, 2 degraded, 0 failed
SMOKE_EXIT=0
```

| Check | Result |
|---|---|
| `GET /api` metadata | PASS |
| `GET /system/health` | PASS (reports db up / redis down / inference unknown) |
| unauthenticated `/students` → 401 | PASS |
| bad credentials → 401 | PASS |
| `POST /auth/token` issues JWT | PASS |
| authenticated `GET /students` | PASS |
| `POST /students` (enroll) | **degraded 503** — "inference service unavailable: redis unavailable: …6379…" (expected here; smoke_test treats 503 as graceful) |
| `GET` unknown student → 404 | PASS |
| `POST/GET/PUT/DELETE /cameras` | PASS |
| `POST /cameras/{id}/start` | **degraded 503** — needs Redis + worker |
| attendance summary / logs / live / 404s | PASS |
| `GET /system/gpu-status` | PASS (service=offline) |
| `GET /docs`, `GET /openapi.json` (36 paths) | PASS |

Both degraded rows are correct behaviour: the honest 503 on enrollment is the
direct result of the BUG-33 fix (before it, this probe returned a misleading
`400 quality check failed: no face detected`, which made smoke_test FAIL).

## 3. Additional targeted probes (live, same server)

14 of 15 passed on the first live run; the one "failure" was a
miscalibrated assertion **of the probe itself** (it demanded ≥ 40 OpenAPI path
items; the API has **36 path items** spanning ~48 route registrations when
method-level routes and the WebSocket are counted, consistent with the audit's
route count and with smoke_test's own passing check).

| Probe | Result |
|---|---|
| `GET /` serves the web UI (html) | PASS |
| `GET /api` metadata json (`docs: /docs`) | PASS |
| `GET /openapi.json` schema loads | PASS (36 paths) |
| unauthenticated `/students` → 401 | PASS |
| bad credentials → 401 | PASS |
| login issues JWT | PASS |
| authenticated `/students` → 200 | PASS |
| garbage token → 401 | PASS |
| `/attendance/logs` includes `total` | PASS |
| `/media/...` without auth → 401 | PASS |
| `/media/...` missing file inside root → 404 | PASS |
| `/media/%2e%2e%2f%2e%2e%2f.env` traversal → 401/404 | PASS |
| `POST /cameras` with `javascript:` RTSP URL → 422 | PASS |
| token whose subject has no DB user → 401 (fail-closed) | PASS |

## 4. Authentication matrix (observed)

| Route | No token | Bad/expired token | Valid token | Token w/o DB user |
|---|---|---|---|---|
| `/`, `/api`, `/docs`, `/openapi.json`, `/static/*` | 200 | 200 | 200 | 200 |
| `/auth/token` (login) | per-credentials | per-credentials | — | — |
| `/system/health`, `/system/gpu-status` | 200 | 200 | 200 | 200 |
| `/students`, `/cameras`, `/periods`, `/alerts`, `/attendance/*` | **401** | **401** | 200 | **401** (BUG-25 fix) |
| `/media/{path}` | **401** | **401** | 200/404, confined (SEC-03 fix) | **401** |
| `/ws` (WebSocket) | close 4401 | close 4401 | events (in-process test) | close 4401 (BUG-24 fix) |

## 5. WebSocket

`/ws` auth cannot be exercised against a bare uvicorn without a Redis pub/sub
backlog in this environment; the same logic is covered in-process
(`tests/test_ws.py`, plus new revocation regression). Live behaviour remains
"verified in-process only" — stated as such, not claimed as live-verified.

## 6. Notes / observations

- **`InsecureKeyLengthWarning`** from PyJWT during the smoke run: the dev
  `JWT_SECRET` default (`change-me-in-production`, 23 bytes) is below the 32-byte
  recommendation. The production startup check trips on exactly this default,
  and the `.env.example` deliberately keeps it so the template can never be
  deployed as-is. Recorded for the security plan (`07`), not changed silently.
- CORS, upload-size limits etc. were not altered (deferred by scope → `07`).
- The compose stack (postgres + redis + GPU inference) is still **unverified
  end-to-end** — no Docker/GPU on this machine. The Dockerfile/compose files
  are structurally fixed (mounts now wired, worker script exists) but must be
  run on a GPU host before being called validated.
