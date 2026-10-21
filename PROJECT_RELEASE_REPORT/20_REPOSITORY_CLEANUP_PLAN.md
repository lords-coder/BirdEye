# 20 — Repository Cleanup Plan

**Date:** 2026-10-21 · **Project:** BirdEye (AI Video Attendance for SRM University Haryana)

---

## 1. Project Inventory

### Root Structure
```
C:\test\projects\ai-video-attendance-submission\
├── .github/                    (not present yet - will be created)
├── .venv/                      (virtual env - EXCLUDE FROM GIT)
├── alembic/                    (migrations - KEEP)
├── app/                        (backend source - KEEP)
│   ├── api/                    (REST endpoints)
│   ├── inference/              (ONNX GPU inference)
│   ├── pipeline/               (camera capture + processing)
│   ├── services/               (alerts, attendance, matching)
│   ├── workers/                (Celery tasks)
│   ├── static/                 (Manus frontend - app.js, index.html, styles.css)
│   └── *.py                    (core modules)
├── build/                      (PyInstaller build cache - REGENERABLE)
├── data/                       (runtime data - EXCLUDE FROM GIT)
│   ├── students/               (reference photos)
│   └── attendance.db           (SQLite dev DB)
├── dist/                       (PyInstaller output - EXCLUDE FROM GIT)
│   └── BirdEye.exe             (370 MB desktop launcher)
├── docs/                       (documentation - KEEP)
│   └── ARCHITECTURE.md
├── PROJECT_*_REPORT/           (technical reports - KEEP/ARCHIVE)
├── samples/                    (synthetic video fixtures - KEEP)
├── scripts/                    (profiling/measurement scripts - KEEP)
├── testing00111/               (isolated frontend experiment - ARCHIVE)
├── tests/                      (test suite - KEEP)
├── .env                        (secrets - EXCLUDE FROM GIT)
├── .env.example                (sanitized template - KEEP)
├── .gitignore                  (UPDATE NEEDED)
├── alembic.ini                 (KEEP)
├── BirdEye.spec                (PyInstaller spec - KEEP)
├── CONTRIBUTING.md             (KEEP)
├── docker-compose.yml          (KEEP)
├── Dockerfile                  (KEEP)
├── dump.rdb                    (Redis dump - EXCLUDE FROM GIT)
├── Makefile                    (KEEP)
├── pyproject.toml              (KEEP)
├── README.md                   (KEEP)
├── requirements-dev.txt        (KEEP)
├── requirements.txt            (KEEP)
├── test_window.html            (trivial test - REGENERABLE)
└── x                           (empty file - REGENERABLE)
```

---

## 2. Classification

| Path | Category | Reason |
|---|---|---|
| `app/` | KEEP | Core backend |
| `app/static/` | KEEP | Manus frontend |
| `alembic/` | KEEP | DB migrations |
| `tests/` | KEEP | Full test suite |
| `scripts/` | KEEP | Profiling + measurement harnesses |
| `samples/` | KEEP | Synthetic video fixtures for reproducible tests |
| `docs/` | KEEP | Architecture docs |
| `PROJECT_*_REPORT/` | KEEP | All technical reports |
| `docker-compose.yml`, `Dockerfile` | KEEP | Deployment |
| `requirements.txt`, `requirements-dev.txt`, `pyproject.toml` | KEEP | Dependencies |
| `alembic.ini` | KEEP | Migration config |
| `BirdEye.spec` | KEEP | Packaging spec |
| `Makefile`, `CONTRIBUTING.md`, `README.md` | KEEP | Dev docs |
| `.env.example` | KEEP | Template |
| `.gitignore` | UPDATE | Needs `build/`, `dist/`, `dump.rdb` |
| `testing00111/` | ARCHIVE | Isolated frontend experiment, useful for reference |
| `.venv/` | EXCLUDE | Virtual environment |
| `data/` | EXCLUDE | Runtime DB + photos |
| `build/` | REGENERABLE | PyInstaller build cache |
| `dist/` | EXCLUDE | Generated 370 MB EXE |
| `dump.rdb` | EXCLUDE | Redis dump |
| `test_window.html` | REGENERABLE | Trivial test page |
| `x` | REGENERABLE | Empty file |

---

## 3. Identified Defects (Fixed in This Session)

| ID | Area | Symptom | Root Cause | Fix |
|---|---|---|---|---|
| D-1 | Camera start | "already starting (slot=1)" wedged forever | `STARTING` state survived 24h TTL with no heartbeat check; worker crash/container restart left stale state | `get_camera_runtime` now validates heartbeat freshness; `stale=True` -> cleared on next start |
| D-2 | Camera start | Duplicate start requests created duplicate pipelines/RTSP connections | No idempotency check for fresh `starting`/`running` states | `start_camera` now returns existing task when heartbeat is fresh |
| D-3 | Camera start | Failed RTSP connection left camera in `STARTING` permanently | No startup deadline; pipeline waited forever in batch collection | Bounded startup timeout (`camera_startup_timeout_s=30s`); deadline stored in Redis; worker and API both enforce; `FAILED` state with error message |
| D-4 | Camera stop | Stop didn't clean up `cam_error_key`, `cam_startup_deadline_key` | Missing delete calls | Added cleanup in `stop_camera` (force) and worker `finally` block |
| D-5 | Slot management | Second camera could steal first camera's slot during dispatch window | Heartbeat written too late | `start_camera` writes heartbeat immediately via `touch_heartbeat` (already present, verified by test `test_immediate_second_start_gets_a_different_slot`) |
| D-6 | State leakage | Worker/API/container restart left phantom running-camera state | No automatic stale-state sweep | `clear_stale_camera_state` called on start; `get_camera_runtime` reports `stale=True` |
| D-7 | Frontend UX | Start button showed misleading "already starting" with no retry path | Backend returned 409 without actionable detail | Idempotent start returns task info; `FAILED` state returns error; live-status exposes `error` and `startup_deadline_at` |

---

## 4. New Settings Added (in `app/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `camera_startup_timeout_s` | 30.0 | Deadline for pipeline to reach RUNNING (first decoded frame) |
| `camera_failed_state_ttl_s` | 600 | TTL for `FAILED` state (auto-clears, allows immediate retry) |
| `camera_error_ttl_s` | 3600 | TTL for human-readable error message |

---

## 5. Files Changed

| File | Change |
|---|---|
| `app/config.py` | +3 new settings |
| `app/redis_client.py` | +`CAMERA_TRANSIENT_STATES`, `get_camera_runtime` validates heartbeat, `clear_stale_camera_state`, new keys `cam_startup_deadline_key`, `cam_error_key` |
| `app/api/cameras.py` | Idempotent start, stale recovery, bounded startup deadline, error cleanup, idempotent response schema |
| `app/pipeline/camera_task.py` | Startup deadline enforcement, first-frame deadline clearance |
| `app/schemas.py` | `StartStopResponse` + `startup_deadline_s`, `error` |
| `tests/test_api.py` | Updated idempotent-start assertion (200 + same task_id) |

---

## 6. Cleanup Actions Required

1. **Delete REGENERABLE files**: `x`, `test_window.html`
2. **Move `testing00111/` to `archive/testing00111/`** (or keep in place with `.gitignore`)
3. **Update `.gitignore`** to exclude `build/`, `dist/`, `dump.rdb`
4. **Verify no secrets in `.env`** (already in gitignore)
5. **Run full test suite** to confirm cleanup doesn't break anything

---

## 7. Git / GitHub Blockers

- **Git not installed** on this machine (no `git` or `gh` CLI in PATH)
- **No local Git repository** exists
- **GitHub repository `BirdEye`** - unknown if exists under authenticated account
- **Approval required** before: installing Git/GitHub CLI, initializing repo, pushing

---

## 8. Next Steps

1. Execute cleanup actions (safe, no service restarts needed)
2. Run full test suite post-cleanup
3. Document changes in `21_CLEANUP_AND_CHANGE_MANIFEST.csv`
4. Perform security scan (`23_SECURITY_AND_SECRET_SCAN.md`)
5. Request approval for Git installation and GitHub publication