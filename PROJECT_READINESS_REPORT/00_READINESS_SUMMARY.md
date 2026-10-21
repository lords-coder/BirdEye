# 00 — Readiness Summary

**Phase:** post-repair validation & readiness (2026-10-14)
**Base:** the repaired tree reported in `PROJECT_REPAIR_REPORT/` (178 passed / 1 skipped at hand-off)
**Rules honoured:** no redesign, no destructive ops, no fabricated evidence, no
silent changes to enrollment rules / quality thresholds / frame requirements /
grace periods (the one frame-rule change was explicitly **approved** — see
`01_DECISION_REGISTER.md` D-1).

## Verdict

**The system is NOT yet production-ready**, but the gap has narrowed sharply.
Everything that could be validated on this machine has been validated with
**real components** — including a full end-to-end attendance run on the real
`buffalo_l` model executing on the **NVIDIA RTX 5060 GPU (CUDA active)**, real
Redis, real Celery worker, real WebSocket feed and real SQLite rows.

Still blocking a production claim:

| Gate | Status |
|---|---|
| Docker/compose runtime on a Docker host | **unverified** — no Docker/WSL on this machine (static validation only, 14/14 PASS + 1 WARN) |
| Live RTSP camera against a real device | **unverified** — no camera hardware (failure/recovery paths validated against a dead RTSP endpoint instead) |
| Real-camera accuracy (TAR/FAR, threshold sweep) | **unverified** — no real footage of enrolled people; `scripts/check_accuracy.py` is ready |
| PostgreSQL (compose DB) runtime | **unverified** — validated against SQLite; migrations verified statically (`alembic check` clean) |
| Security plan P2/P3 items (JWT transport, WS token, secrets, reset throttling) | **open** — see `03_SECURITY_STATUS.md` |

## What was proven in this phase (real components, real runs)

1. **GPU inference ACTIVE on the real model.** `onnxruntime-gpu==1.26.0`
   (the pin already documented in `requirements.txt`) + CUDA-12 pip wheels on
   the RTX 5060. Canary: `ACTIVE session providers: ['CUDAExecutionProvider',
   'CPUExecutionProvider']`, detection ≈ 0.10–0.16 s/frame (≈ 5× CPU).
   Reproduce: `python scripts/validate_inference.py`.
2. **Full-stack E2E attendance, 17/17 checks green**
   (`scripts/validate_e2e.py`): real quality-gate enrollment, duplicate-identity
   409 on a second registration of the same face, a second identity enrolled,
   file-source camera running the real pipeline → recognition events for both
   identities, sessions opened, `/attendance/summary` = both PRESENT,
   WebSocket live feed delivered `recognition`/`alert`/`camera_state`/
   `unknown_face` events, clean stop, full cleanup.
3. **Entry→exit lifecycle**: exit-type camera closed both sessions
   (224 s / 223 s durations, `completed_sessions=2`).
4. **Inference-outage recovery**: service killed mid-run → API add-photos
   returned **503** (not a misleading 400), camera pipeline survived (fresh
   heartbeats), service restarted → recognition resumed (logs 6→9, 9→12 across
   two runs). Worker log shows the designed 2 s→4 s→8 s→16 s RPC backoff.
5. **RTSP failure recovery**: dead `rtsp://` source → pipeline stays alive,
   OpenCV open-failures retried with 2 s→4 s backoff, clean stop afterwards.
6. **Redis validated for real** (portable Redis 5.0.14.1 on 127.0.0.1:6379):
   LPUSH/BLPOP RPC round-trip, camera slots, dedup gates, WS pub/sub backlog.
7. **Dependency audit closed** (owner-approved): `python-multipart` 0.0.12 →
   0.0.31 (7 advisories) and coordinated `fastapi` 0.115.0 → 0.143.1 +
   `starlette` 1.8.0 (14 advisories). `pip-audit -r requirements.txt` now
   reports **0 known vulnerabilities**; the 195-test suite is green on the
   upgraded stack.
8. **Static compose validation** (`scripts/validate_compose.py`, 14 PASS /
   1 WARN / 0 FAIL) which found and fixed two real deployment defects
   (DEF-35): the `insightface_models` volume would mount root-owned (engine
   could not write weights as `appuser`) and nothing bootstrapped the model
   weights in the inference container.

## What was fixed in code this phase

| ID | Defect / gap | Fix |
|---|---|---|
| DEF-34 | `download_models.py` failed on any fresh machine (missing `models/` dir; official flat zip layout unhandled) — hidden until now because `setup_dev` runs were `SKIP_MODELS=1` | mkdir before write; flat-layout recovery; 3 regression tests |
| DEF-35 | compose: root-owned `/models` volume + no weight bootstrap in the inference service | Dockerfile chown; `download_models.py` in the inference command; static validator to keep it honest |
| D-1 | frame-confirmation rule divergence (config 2/5 vs acceptance doc 3/10) | defaults now 3 frames / 10 s (approved) |
| D-2 | quality gate missing on `POST /{id}/photos` + `PUT /{id}/face` | shared `_quality_gate_or_raise` on all entry points (approved) |
| SEC-04/11 | production could boot with default `ADMIN_PASSWORD` / `CORS_ORIGINS=*` | startup hard-fails (approved via security plan) |
| SEC-05 | no login throttling | Redis-backed per-(ip,user)+per-ip fixed window, 429, fail-open on Redis outage |
| SEC-10 | unbounded request bodies | `MAX_UPLOAD_BYTES` (default 25 MB) → 413 |
| SEC-13 | `check_accuracy.py` fell back to admin/admin | explicit env credentials required |
| SEC-16 | dependency vulnerabilities | audited + remediated to zero (owner-approved upgrades) |

## Honest classification of every validation in this report

* **Tested with real components** — GPU model inference, RPC over real Redis,
  Celery camera pipeline, WebSocket feed, SQLite rows, JWT auth matrix,
  outage/RTSP failure behaviour, E2E enrollment→attendance.
* **Simulated / synthetic** — camera *sources* are generated MP4 slideshows of
  public-domain faces (no physical camera); the "dead RTSP" is an unreachable
  endpoint, not a flapping real link.
* **Unverified (environment-blocked)** — Docker runtime, PostgreSQL runtime,
  real RTSP device behaviour, real-camera accuracy, multi-camera GPU batching
  under load, TLS/reverse-proxy behaviour.

## Reproducing everything

Commands for every claim above (and the exact environment needed) are in
`02_ENVIRONMENT_VALIDATION.md`; acceptance evidence paths in
`04_ACCEPTANCE_TESTS.md`.
