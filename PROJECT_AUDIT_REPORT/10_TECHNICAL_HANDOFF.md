# 10 — Technical Handoff

Everything a new developer or AI agent needs to work on this project **without rediscovering it**. All statements are evidence-backed (file:line). No secrets are included.

## 1. What this is

`ai-video-attendance` v1.0.0 — a camera-based attendance backend: detect faces in RTSP/video (InsightFace `buffalo_l` = SCRFD + ArcFace 512-d), match against an enrolled gallery (cosine: threshold 0.40 → margin 0.10 → duplicate-identity skip 0.90), record attendance sessions against timetable periods, expose REST + WebSocket, and serve a bundled vanilla-JS admin SPA.

**Current overall state:** well-built domain core, broken packaging, several confirmed defects. Read `PROJECT_AUDIT_REPORT/00_EXECUTIVE_SUMMARY.md` and `05_BUGS_AND_DEFECTS.md` first. **12 report files in this directory are the complete audit; do not re-audit.**

## 2. Technology (verified)

Python ≥3.11 · FastAPI 0.115 · Pydantic 2.9 (v2) · SQLAlchemy 2.0.35 (typed `Mapped[]`) · Alembic 1.14 · PostgreSQL 16 (psycopg 3.3.6) in prod, SQLite in dev/tests · Redis 7.4.1 (broker, RPC bus, pub/sub, runtime state) · Celery 5.6.3 (NOT `celery[redis]` — documented conflict with redis 7.x) · PyJWT 2.15 HS256 · cryptography/Fernet · insightface 2.0 + onnxruntime + opencv + numpy 2.1.3 · nvidia-ml-py · vanilla JS frontend (no build step) · pytest + httpx + fakeredis (dev).

Authoritative dependency file: **`requirements.txt`** (fully pinned). `pyproject.toml` disagrees on `celery[redis]` — ignore it for installs.

## 3. Run book (as the repo actually stands)

- **Cannot** `docker compose up` — no `Dockerfile`, no `scripts/run_worker.sh` (BUG-02). Don't try; fix first.
- **API locally:** needs Python 3.11+, `pip install -r requirements.txt`, a reachable DB (`DATABASE_URL`; default is Postgres at localhost — override with SQLite for dev), optionally Redis. Then `uvicorn app.main:app --reload`. The API boots without GPU/cv2 (heavy imports are lazy — verified).
- **Migrations:** `alembic upgrade head` (`alembic/env.py` injects `DATABASE_URL`). ⚠ `0001` creates `students.embeddings` as JSON while the model says String (BUG-04) — fix via migration 0005 before trusting a migrated DB.
- **Auth:** first `/auth/token` seeds `ADMIN_USERNAME`/`ADMIN_PASSWORD` (default **admin/admin**) as active super_admin. All other routes need `Authorization: Bearer <jwt>` unless `AUTH_REQUIRED=false` (which makes everyone super_admin — dev only).
- **Tests:** `python -m pytest` — 148 test functions, no external services needed (fakeredis, stubbed Celery, per-run temp SQLite via `tests/conftest.py` which pins env **before importing app**). ⚠ Tests use `create_all`, so migration bugs are invisible to them.
- **Inference service:** `python -m app.inference.server` (needs insightface weights in `INSIGHTFACE_ROOT`, default `~/.insightface`, downloaded via `scripts/download_models.py`).
- **Worker:** compose says `bash scripts/run_worker.sh` (missing). Correct launch would be `celery -A app.workers.celery_app worker` consuming `control`, `jobs`, and `camera_slot_0..CAMERA_SLOTS-1` queues; beat schedule defined in `app/workers/celery_app.py:44-58`.

## 4. Architecture invariants (do not break)

1. **The API process must never import cv2/onnxruntime/insightface.** Keep heavy imports function-local (`engine.py`, `frame_reader.py`, `server.py`); API→inference goes through Redis RPC only (`inference/client.py`).
2. **One threshold source of truth:** `RECOGNITION_THRESHOLD`/`RECOGNITION_MARGIN`/`DUPLICATE_IDENTITY_SIM` in `app/config.py` with measured derivations in comments. Don't hardcode scores (CONTRIBUTING rule 4). Note the existing violation: `quality.py:197` uses the wrong setting (BUG-17).
3. **Embeddings are encrypted at rest** (`app/crypto.py`, Fernet; key from `EMBEDDING_ENCRYPTION_KEY`, production hard-fail, no JWT fallback — deliberate). Never log or return plaintext embeddings except via the explicit `include_embeddings` query flag.
4. **Privacy rules (CONTRIBUTING):** no real names/registration numbers in tracked files, logs, or reports; output uses `P1..Pn` + last-3 digits; placeholder photos use `SAMPLE###` ids.
5. **Camera slots:** start claims a Redis NX slot and dispatches `send_task("camera.run", queue=slot_queue)` by name string — API and worker stay import-decoupled. Don't add static imports of `app.tasks` into the API.
6. **Session state machine:** `services/attendance.py` owns open/close; `date` column is the **local** calendar day (`timeutil.local_date`), timestamps are UTC.

## 5. Key files (the 20 that matter)

| File | Role |
|---|---|
| `app/main.py` | app factory, router mounting, `/media`+`/static` mounts (⚠ `/media` is unauthenticated — SEC-03) |
| `app/config.py` | every setting + threshold rationale; production validators |
| `app/models.py` | 8 tables (Student, Camera, Period, AttendanceSession, Alert, User, PasswordResetRequest, RecognitionLog) |
| `app/schemas.py` | all Pydantic models (⚠ `CameraOut` inherits a validator that can 500 — BUG-23) |
| `app/security.py` | JWT + Principal + guards (⚠ legacy-token fallback — BUG-28/SEC-08) |
| `app/users.py` | PBKDF2 hashing, account lifecycle, seed superadmin, `.env` password re-sync |
| `app/crypto.py` | Fernet embedding encryption (import-time PBKDF2) |
| `app/matching.py` | `EmbeddingIndex` (refresh via Redis `students:version`; threshold→margin→dup-skip) |
| `app/services/enrollment.py` | enrollment + bulk CSV (⚠ `add_photos` BUG-01) |
| `app/services/attendance.py` | session state machine + summaries (~90 % duplicated pairs — P3-1) |
| `app/pipeline/camera_task.py` | live per-camera loop (⚠ no per-iteration try/except — BUG-15) |
| `app/pipeline/frame_reader.py` | decode thread (⚠ process-global RTSP env — BUG-26) |
| `app/inference/server.py` | GPU service (⚠ batch cap counts requests — BUG-11) |
| `app/inference/client.py` | RPC client (⚠ exception order — BUG-14) |
| `app/workers/celery_app.py` | queues + beat (⚠ dead route `camera_slot_dynamic` — BUG-12) |
| `app/tasks.py` | Celery tasks (untested module) |
| `app/api/*` | 46 routes (see `03_API_AND_DATABASE.md` for the full inventory) |
| `app/static/app.js` | SPA (⚠ XSS sink `:421`, reconnect bug `:578`) |
| `tests/conftest.py` | env pinning + fakes — read before writing tests |
| `docker-compose.yml` | 5-service topology (⚠ currently unbuildable) |

## 6. Route map (fast reference)

`/auth/token|register|forgot-password|me` · `/auth/admin/users[/{id}[/approve|reject|suspend]]` (PATCH for edits) · `/auth/admin/password-requests[/{id}]` · `/students[|/{id}[/photos|/face]|/bulk-import]` · `/cameras[|/{id}[/start|/stop]]` · `/attendance/summary|live|logs|period/{pid}/summary|period/{pid}/{sid}|{sid}|sessions/{id}/close` · `/periods[|/{id}]` · `/alerts[|/{id}[/ack|/resolve]]` · `/system/health|gpu-status` · `WS /ws/live-feed?token=` · `/` (SPA) · `/api` (metadata). Full table incl. auth requirements and frontend consumers: `03_API_AND_DATABASE.md` §1.

## 7. Known defects — read before changing anything

Authoritative list: `05_BUGS_AND_DEFECTS.md` (30 confirmed BUGs, 5 SUS, unverified list) + `06_SECURITY_AUDIT.md` (17 SEC items). The ones that will bite you first:

1. **BUG-01** `add_photos` counts ciphertext chars → endpoint always fails. Any fix here needs the P1-1 regression test.
2. **BUG-04** migration/model type mismatch on `students.embeddings` → tests won't catch it (they bypass migrations).
3. **BUG-11/14/15** inference/pipeline latent bugs — the suite has **zero coverage** of `tasks.py`, `inference/server.py`, `inference/client.py`, `frame_reader.py`, `camera_task.py`; treat any change there as untested until P2-2/P2-6 land.
4. **BUG-05/06** frontend logout leaks timers → any WS work must fix the `getToken()` contract first.
5. **BUG-03** `judge_demo.py:244` → `main(argv)` TypeError (masked by test monkeypatching).

## 8. Conventions observed in this codebase

- `from __future__ import annotations` everywhere; type hints on new code; functions small and boring.
- Routes stay thin; business logic in `app/services/`; schemas centralized in `app/schemas.py`.
- Errors: raise `HTTPException` in routes; domain exceptions (`EnrollmentError`, `InferenceError`) mapped at the edge; global 500 handler logs traceback, returns generic detail.
- Sessions: `autoflush=False` + `expire_on_commit=False` (`db.py:27-29`) — remember explicit `db.flush()` where ordering matters (see `attendance.py:97`) and be careful reading attributes after `rollback()`.
- Tests: fixtures `client`, `auth_header`, `db`, `fake_embed`, `fake_redis` (autouse), `fake_celery` (autouse). Use `make_photos()` from conftest for multipart uploads.
- Logging: `logging_config.configure` at process start; use `logging.getLogger(__name__)` (never `print` — 7 existing violations slated for removal, P0-5).

## 9. Environment setup for the next agent

1. Install Python 3.11+ (this audit machine had **none** — that's why nothing could be executed).
2. `pip install -r requirements.txt` + dev set (pytest, httpx, fakeredis; `requirements-dev.txt` is missing — P1-5).
3. Provide `.env` from the README §8 table (no `.env.example` exists yet); set `DATABASE_URL=sqlite:///./dev.db` for local work; `FORCE_CPU=true` without a GPU.
4. Run `python -m pytest` and confirm the count ≥148 before trusting any change.
5. GPU/RTSP/Postgres paths remain unverifiable without hardware; label such work UNVERIFIED honestly (this project's culture values that — see README §1 "Honesty note").

## 10. Unresolved questions for the owner

1. Should `eval/` (ROADMAP.md + calibration tools) be restored from the original source, or should all references be deleted? Multiple docs depend on it.
2. Is the Google-Forms pipeline (`formcsv.py`, `setup_student_database.py`, `judge_demo.py`, `test_cctv.py`) a deliverable feature or scratch work? It's ~1,500 lines, tested, but disconnected from the app.
3. What should `security_level` (0-5) actually gate? It's collected and displayed but never enforced.
4. Which deployment target is canonical — Docker Compose (GPU host) or the single-process demo? This determines whether P1-2 or the demo path gets priority.
5. Is there an upstream git history? This archive has no `.git`; recovering it would tell us whether the missing files were lost in packaging or never existed.
6. Acceptance floor: CONTRIBUTING says the suite floor is "> 51 tests" and mentions a 3-frame/10-s minimum-frames acceptance rule — confirm these criteria still apply post-repair (the live config currently uses 2 frames/5 s).
