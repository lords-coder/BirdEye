# 06 — Remaining Issues

Everything known-unresolved after this repair phase, with why. Nothing here
was silently ignored; each item is either environment-blocked, deliberately
deferred (scope), or awaiting a product decision.

> **Readiness-phase status update (2026-10-14):** items resolved since this
> document was written are struck through in place; see
> `PROJECT_READINESS_REPORT/` for full evidence. Several environment-blocked
> rows below are now **validated natively** (GPU inference, WS live feed,
> Redis paths, outage recovery, RTSP failure recovery) — only the
> Docker-compose runtime, PostgreSQL runtime, real camera hardware and
> real-camera accuracy remain unverified.

## 1. Environment-blocked (cannot be verified on this machine)

| Issue | Blocker | What to do on a capable host | Readiness status |
|---|---|---|---|
| ~~GPU compose stack never run~~ | no Docker here | `docker compose up -d`, then `scripts/smoke_test.py` — enrollment should go 201 and camera start 200 | **Native equivalent fully validated 2026-10-14** (Redis + inference-GPU + API + Celery, E2E 17/17); compose runtime itself still needs a Docker host |
| ~~Live RTSP camera never exercised~~ | no camera | point a camera at `POST /cameras` with a real `rtsp://` URL, start the slot, watch `/attendance/live` | **Failure/recovery validated** against a dead RTSP endpoint (backoff, survival, clean stop); a *real* RTSP device still needs hardware |
| Real-camera recognition accuracy unmeasured | no real footage of enrolled people | `scripts/check_accuracy.py` (TAR/FAR/rank-1 + threshold sweep) on your own gallery | still open — decision O-2 in `PROJECT_READINESS_REPORT/01_DECISION_REGISTER.md` |
| ~~WebSocket live feed not exercised against a live Redis~~ | no Redis | in-process tests pass; run a camera on compose and watch `/ws` | **Validated 2026-10-14**: live `/ws/live-feed` delivered recognition/alert/camera_state/unknown_face events against portable Redis |
| PostgreSQL runtime + PG migrations | no PostgreSQL here | `docker compose up postgres`, `alembic upgrade head` | still open (`alembic check` clean statically; PG `USING` path of 0005 exercised only on SQLite) |

## 2. Deferred defects (known, low priority, planned)

| ID | Issue | Why deferred | Plan |
|---|---|---|---|
| BUG-26 | `OPENCV_FFMPEG_CAPTURE_OPTIONS` set process-globally → per-camera transport is first-writer-wins in multi-camera workers | touches the hot reader; needs multi-camera RTSP to verify (blocked above) | rework to per-`VideoCapture` properties when a camera lab exists |
| BUG-27 | Events drop a legitimate `0.0 ms` latency (`if inference_ms else None`); datetimes serialized via `default=str` | cosmetic contract drift, no consumer broken | one-line fix + response-model datetime serializer in the next pass |
| BUG-19 (part) | `inference/local.py` pads unreadable frames black where the server rejects them | script-side debug helper only | align behaviour when the eval toolkit is rebuilt |
| — | `engine.py` live path enforces only area/brightness/sharpness gates; config's pose/width/det-score gates are not applied on the live path | GPU module, unverifiable here; changing it alters recognition behaviour | decide after the first real-camera calibration |
| — | Some list endpoints return bare `dict` (no response_model) | cosmetic OpenAPI contract gap | add response models in the next pass |

## 3. Missing `eval/` toolkit — recorded, NOT fabricated

Older docs referenced a 7-file `eval/` toolkit (`ROADMAP.md`,
`enroll_and_calibrate.py`, `diag_threshold.py`, `PROGRESS.md`, …). **It is not
in this archive and was not reconstructed** — inventing research tooling and
its measured numbers would be dishonest. All references in README,
CONTRIBUTING, `docs/ARCHITECTURE.md` and `run_demo.py` now either point to the
shipped alternative (`scripts/check_accuracy.py`) or state the gap explicitly.
Rebuilding the toolkit is roadmap item #1 in the audit's fix roadmap.

## 4. Decisions needed from the project owner

> **Resolved 2026-10-14** (owner decisions, full register in
> `PROJECT_READINESS_REPORT/01_DECISION_REGISTER.md`):
> **1 → adopted 3 frames / 10 s** (config defaults changed to match the
> CONTRIBUTING acceptance criterion); **2 → add/replace photos are now
> gated**; **3 → 15/5/10 confirmed** as institutional defaults;
> **5 → `check_accuracy.py` requires explicit credentials** (admin/admin
> fallback removed). Remaining: **4 (confirm.py/formcsv.py/evalmetrics.py
> fate — delete or unify)** plus new items O-2/O-3/O-5 in the register.

1. ~~Minimum-frames confirmation rule.~~ **Resolved: 3 frames / 10 s (D-1).**
2. ~~Quality gate coverage.~~ **Resolved: gated everywhere (D-2).**
3. ~~Institutional policy values.~~ **Resolved: 15/5/10 confirmed (D-3).**
4. **`confirm.py` / `formcsv.py` / `evalmetrics.py`** remain disconnected
   script-side modules with constants that diverge from `config.py`
   (sharpness 40 vs 30, brightness max 225 vs 230). Delete, or unify?
   — still open (O-1).
5. ~~`scripts/check_accuracy.py` fallback credentials.~~ **Resolved (D-4).**

## 5. Local-environment notes (not defects)

- ~~No Redis on this machine~~ → **Redis 5.0.14.1 (portable) was run for the
  validation pass**; the graceful-degradation behaviour remains true when it
  is absent. `docker compose up -d redis` or a local Redis removes the two
  "degraded" smoke rows.
- PyJWT prints `InsecureKeyLengthWarning` for the dev-default JWT secret; the
  production startup check fails on that same default by design.
- `.env` and `data/attendance.db` exist locally (gitignored). Deleting them is
  safe; `scripts/setup_dev.ps1` recreates both.
- **New (readiness):** this machine now has `onnxruntime-gpu==1.26.0` +
  CUDA-12 wheels in `.venv` and weights under `~/.insightface` (gitignored).
  CPU-only hosts keep working — the engine falls back with a loud warning
  (observed live).
