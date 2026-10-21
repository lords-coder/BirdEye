# 01 — Project Structure

**Root:** `C:\test\projects\ai-video-attendance-submission`
**104 meaningful files** (excluding `.pytest_cache/` and `__pycache__/` contents). No `.git` directory is present — this is an extracted archive, not a working git checkout. No hidden configuration files (`.gitignore`, `.env`, `.env.example`) exist anywhere in the tree.

## 1. Directory tree (annotated)

```
ai-video-attendance-submission/
├── app/                          # THE application package (46 Python files + 3 static assets)
│   ├── __init__.py               # __version__ = "1.0.0"
│   ├── main.py                   # FastAPI entrypoint: lifespan, CORS, router mounting, /media + /static mounts
│   ├── config.py                 # pydantic-settings; every tunable + measured threshold derivations
│   ├── models.py                 # 8 SQLAlchemy models (Student, Camera, Period, AttendanceSession,
│   │                             #   Alert, User, PasswordResetRequest, RecognitionLog)
│   ├── schemas.py                # All Pydantic v2 request/response models
│   ├── db.py                     # Engine + SessionLocal (autoflush=False, expire_on_commit=False)
│   ├── security.py               # JWT issue/decode, Principal, role guards
│   ├── users.py                  # PBKDF2 password hashing, account statuses, seed super-admin
│   ├── crypto.py                 # Fernet encryption of embeddings at rest (PBKDF2-derived key)
│   ├── matching.py               # EmbeddingIndex: threshold → margin → duplicate-identity rules
│   ├── quality.py                # Per-photo enrollment quality gate + cross-student duplicate check
│   ├── confirm.py                # Offline K-of-N confirmation / box tracker / ledger (scripts+tests only)
│   ├── formcsv.py                # Google-Forms CSV pipeline helpers (scripts+tests only)
│   ├── evalmetrics.py            # Identity metrics: FAR/FRR/precision/recall (scripts+tests only)
│   ├── tasks.py                  # Celery tasks: camera.run + 3 maintenance jobs (loaded via Celery include)
│   ├── timeutil.py               # UTC/LOCAL timezone helpers
│   ├── redis_client.py           # Redis pools, camera runtime keys, dedup gate, pub/sub, version counter
│   ├── logging_config.py         # JSON/text logging setup
│   ├── api/                      # Thin HTTP layer (10 files)
│   │   ├── auth.py               # 11 routes: token, register, forgot-password, me, admin user mgmt
│   │   ├── students.py           #  9 routes: CRUD, photos, face, bulk-import
│   │   ├── cameras.py            #  7 routes: registry + start/stop pipeline slots
│   │   ├── attendance.py         #  7 routes: summary, live, logs, per-period, close session
│   │   ├── periods.py            #  5 routes: timetable CRUD
│   │   ├── alerts.py             #  4 routes: list/get/ack/resolve
│   │   ├── system.py             #  2 routes: /system/health, /system/gpu-status
│   │   ├── ws.py                 #  WS /ws/live-feed (backlog replay + Redis pub/sub)
│   │   ├── deps.py               # re-exports get_db / get_current_admin
│   │   └── __init__.py
│   ├── inference/                # GPU layer (5 files)
│   │   ├── engine.py             # FaceEngine: SCRFD batched detect → align/quality → ArcFace embed
│   │   ├── server.py             # Shared GPU service: BLPOP infer:requests, cross-camera batching
│   │   ├── client.py             # Redis RPC client (infer_frames / embed_images)
│   │   ├── local.py              # In-process engine for scripts (monkeypatches client)
│   │   └── __init__.py
│   ├── pipeline/                 # Live video path (3 files)
│   │   ├── frame_reader.py       # cv2.VideoCapture thread: sampling, bounded drop-oldest queue, reconnect
│   │   ├── camera_task.py        # Per-camera loop: batch → RPC → match → confirm → dedup → record
│   │   └── __init__.py
│   ├── services/                 # Business logic (5 files)
│   │   ├── enrollment.py         # Enrollment, bulk CSV import, photo persistence (⚠ contains Bug #1)
│   │   ├── attendance.py         # Session open/close state machine, summaries, live view
│   │   ├── alerts.py             # Alert creation + period deviation evaluation (beat job)
│   │   ├── events.py             # WebSocket event payload builders + publish()
│   │   └── __init__.py
│   ├── workers/
│   │   ├── celery_app.py         # Celery topology: control, jobs, camera_slot_0..N queues + beat schedule
│   │   └── __init__.py
│   └── static/                   # Bundled SPA (served at /)
│       ├── index.html            # 326 lines; 8 views (login/register/forgot/app with 7 sections)
│       ├── app.js                # 823 lines; all fetch/WS logic
│       └── styles.css            # 156 lines
├── tests/                        # 15 files: conftest + 13 test modules + __init__ (148 test functions)
├── scripts/                      # 18 operational/CLI scripts (see 02_COMPONENT_INVENTORY.md)
├── alembic/                      # env.py + versions/0001..0004 (chain intact)
├── docs/ARCHITECTURE.md          # Module map, data flow, threshold derivations
├── samples/README.md             # Explains videos/faces layout — but the media directories are absent
├── app-adjacent root debris:      # ⚠ 12 ad-hoc scripts that do not belong in a submission
│   ├── add_fallback.py, add_require_all.py, add_require_enforce.py,
│   │   add_require_logic.py, apply_fix.py, fix_require_all.py   # in-place patchers of
│   │                                                            #   scripts/setup_student_database.py
│   ├── analyze_csv.py, list_students.py, test_drive.py, test_drive2.py  # CSV scratch explorers
│   │                                                            #   (test_drive*.py leak real PII — see 06)
│   └── create_zip.py, build_handoff.py                          # archive tooling referencing missing files
├── pyproject.toml                # metadata, deps (ranges), pytest config (addopts="-q")
├── requirements.txt              # PINNED production deps (the verified set)
├── docker-compose.yml            # postgres/redis/api/inference/worker (⚠ requires missing Dockerfile +
│                                 #   missing scripts/run_worker.sh)
├── alembic.ini                   # empty placeholder sqlalchemy.url (env.py injects DATABASE_URL)
├── README.md                     # ⚠ substantially stale (see 08)
└── CONTRIBUTING.md               # ⚠ references missing setup_dev scripts, .gitignore, eval/PROGRESS.md
```

## 2. Technology stack (verified from manifests)

| Concern | Technology | Version evidence |
|---|---|---|
| Language | Python ≥ 3.11 | `pyproject.toml:6` |
| Web framework | FastAPI | `fastapi==0.115.0` (`requirements.txt:30`) |
| Validation | Pydantic v2 + pydantic-settings | 2.9.2 / 2.5.2 |
| ORM | SQLAlchemy 2.0 (typed `Mapped[]`) | 2.0.35 |
| Migrations | Alembic | 1.14.0 |
| Database | PostgreSQL 16 (prod, psycopg 3.3.6) / SQLite (dev+tests) | `docker-compose.yml:32`, `config.py:38` |
| Cache / bus / RPC | Redis 7 (redis-py 7.4.1) | `docker-compose.yml:47`, `requirements.txt:49` |
| Background jobs | Celery 5.6.3 (deliberately *not* `celery[redis]` — documented conflict) | `requirements.txt:43-49` |
| Auth | PyJWT 2.15 (HS256), PBKDF2-SHA256 in-app | `requirements.txt:52`, `app/users.py:61-85` |
| Encryption | `cryptography` 50.0.2 → Fernet | `requirements.txt:58`, `app/crypto.py` |
| ML | insightface 2.0 (`buffalo_l` = SCRFD + ArcFace 512-d), onnxruntime, opencv, numpy 2.1.3 | `requirements.txt:62-63` |
| GPU telemetry | nvidia-ml-py 12.560.30 (pynvml) | `requirements.txt:66` |
| Frontend | Vanilla HTML/CSS/JS SPA (no framework, no build step, no npm) | `app/static/*` |
| Test | pytest (+ httpx TestClient, fakeredis) | `pyproject.toml:27-32` (dev extra) |
| Packaging | setuptools | `pyproject.toml:34-39` |

**Note:** `pyproject.toml:17` declares `celery[redis]>=5.4` while `requirements.txt` deliberately avoids that extra (documented `ResolutionImpossible` with `redis==7.4.1`). Two manifests disagree; `requirements.txt` is the authoritative pinned set.

## 3. Entry points

| Entry point | How it starts | Status |
|---|---|---|
| REST API | `uvicorn app.main:app` (README, compose api service) | Code present; boots without GPU (lazy heavy imports verified) |
| Inference service | `python -m app.inference.server` (`docker-compose.yml:89`) | Code present; requires insightface/onnxruntime |
| Celery worker | `bash scripts/run_worker.sh` (`docker-compose.yml:106`) | **BROKEN — file does not exist** |
| Web UI | `GET /` serves `app/static/index.html` (`app/main.py:112-117`) | Present |
| Demo pipeline | `scripts/run_demo.py` | Present; hardcodes a personal video path default (`scripts/run_demo.py:63`) |
| Migrations | `alembic upgrade head` (`alembic/env.py` injects `DATABASE_URL`) | Present; **type mismatch in 0001** (see 05) |
| Tests | `python -m pytest` | **BLOCKED in this environment (no Python interpreter)** |

## 4. Configuration

- All settings flow through `app/config.py` (`pydantic-settings`, reads `.env` if present, case-insensitive env vars). **No `.env` and no `.env.example` exist in the archive** — the documented `cp .env.example .env` step (`README.md:371`) cannot be performed. The full expected key set is documented in README §8 and matches `config.py` field-for-field (verified), except that README lists `LOCAL_TIMEZONE` default `Asia/Kolkata` while `config.py:133` defaults to `UTC` — another doc drift.
- Production hard-guards: `EMBEDDING_ENCRYPTION_KEY` required (`config.py:183-201`), default `JWT_SECRET` rejected (`config.py:176-181`). Both only active when `ENVIRONMENT=production`.
- Deployment config: `docker-compose.yml` (5 services). No CI/CD workflows (`.github/`, `.gitlab-ci.yml` absent), no Kubernetes/Terraform, no Dockerfile.

## 5. Notable structural observations

1. **Lazy heavy imports are deliberate and correct**: `app/main.py` docstring promises the API never loads InsightFace/CUDA — verified: cv2/onnxruntime/insightface are imported inside functions in `engine.py`, `frame_reader.py`, `server.py`; the API process only ever uses `inference/client.py` (Redis RPC).
2. **Three "shadow" modules** (`app/confirm.py`, `app/formcsv.py`, `app/evalmetrics.py`, ~830 lines total) are imported **only by scripts and tests**, never by runtime `app/` code — parallel implementations of pipeline-adjacent logic with *different* constants than the live path (e.g., `confirm.py` documents 3-frames/10s while `config.py:107-108` uses 2/5s).
3. **Repository hygiene**: 12 root-level scripts are development debris; 4 reference a personal path `C:\Users\pc\...` that doesn't exist here; 6 patch `scripts/setup_student_database.py` in place (one, `add_require_all.py`, appears syntactically invalid); `create_zip.py:115` references a nonexistent `scripts/test_judge_demo.py`.
4. **Media/asset expectations unfulfilled**: `samples/README.md`, `docker-compose.yml:70,111` (`SEED_VIDEO_PATH=/samples/videos/lecture.mp4`), and `config.py:136` all expect `samples/videos/lecture.mp4`; the directory does not exist.
