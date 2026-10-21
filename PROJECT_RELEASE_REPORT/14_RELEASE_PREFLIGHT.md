# 14 — Release Preflight (Stage 0)

**Date:** 2026-10-20  
**Purpose:** Read-only inspection of the BirdEye project before any deployment or packaging operations.

---

## 1. Project Overview

**Repository Root:** `C:\test\projects\ai-video-attendance-submission`  
**Application Name:** AI Video Attendance System  
**Target Institution:** SRM University Haryana

---

## 2. Frontend (Actual — Not Manus.ai)

**Location:** `app/static/`  
**Framework:** Vanilla JavaScript (ES6+) — **NOT** a Manus.ai frontend  
**Files:**
- `app/static/index.html` — SPA with login, dashboard, students, cameras, attendance, periods, history, live, monitor, teacher, users, system sections
- `app/static/app.js` — ~1,300 lines of vanilla ES6 JavaScript (ES modules not used, single-file)
- `app/static/styles.css` — ~8 KB CSS

**API Integration:** Relative fetch paths (`/auth/token`, `/cameras`, `/students`, `/attendance/...`, `/system/health`, `/cameras/{id}/live.mjpeg`, `/ws/live-feed` WebSocket)

**Key Features Implemented:**
- Login/register/forgot-password with JWT auth
- Student enrollment (3-5 photos, face quality gates)
- Camera management (structured RTSP config, encrypted passwords, test/preview)
- Camera preview (authenticated snapshot stream, no credential leak)
- Live monitor (WebSocket events, MJPEG stream from shared pipeline)
- Attendance dashboard (summary, logs, live sessions)
- Period management (timetable, required minutes, attendance computation)
- History (entry/exit intervals, filters)
- Teacher portal (course-scoped periods, student attendance)
- User management (super admin only: approve, credentials, password resets)
- System health/GPU status
- Override modal (manual attendance corrections with reason)

**NOT a Manus.ai frontend.** The task description referenced "Manus.ai frontend" but the actual codebase contains a custom vanilla JS SPA.

---

## 3. Backend Architecture

**Framework:** FastAPI 0.143.1 + SQLAlchemy 2.0.35 + Alembic 1.14.0  
**ASGI Server:** Uvicorn  
**Task Queue:** Celery 5.6.3 (Redis broker/backend)  
**Inference:** InsightFace 2.0 + ONNX Runtime GPU 1.26.0 (shared GPU service)  
**Database ORM:** SQLAlchemy 2.0 with declarative models  
**Auth:** JWT (HS256), PBKDF2-SHA256 password hashing  
**Encryption:** Fernet (embeddings, camera passwords) + PBKDF2 for passwords

**API Routes:**
- `/auth/*` — token, register, forgot-password, me, admin users/passwords
- `/students/*` — CRUD, bulk CSV import, photo upload, face data
- `/cameras/*` — CRUD, start/stop, test-connection, snapshot, stream.mjpeg, live.mjpeg, live-status
- `/attendance/*` — summary, logs, history, period records, overrides
- `/courses/*`, `/periods/*` — course/period management
- `/system/*` — health, GPU status
- `/ws/live-feed` — WebSocket for real-time events

**Key Modules:**
- `app/api/cameras.py` — camera CRUD, pipeline control, secure preview
- `app/pipeline/camera_task.py` — frame reader, batching, inference RPC, presence/attendance logic
- `app/inference/server.py` — shared GPU inference service (Redis RPC)
- `app/services/attendance.py`, `presence.py`, `alerts.py`
- `app/services/camera_test.py` — classified connection diagnostics (auth_failed, unreachable, timeout, no_frames, unsupported, error)

---

## 4. Database

**Current (Dev):** SQLite — `data/attendance.db` (1.1 MB)  
**Alembic:** Head = `0006_camera_attendance` (stamped)  
**Migrations:** 6 revisions (initial → periods → alerts → users → embeddings_text → camera_attendance)

**Current Data (SQLite):**
| Table | Rows |
|-------|------|
| users | 1 (admin / super_admin) |
| students | 1 (Karan Agrawal, 11925210179, 3 photos, embeddings) |
| cameras | 2 (Exterior doorway_exterior, Interior classroom_interior) |
| attendance_sessions | 2 (1 completed, 1 ongoing) |
| recognition_logs | 2,178 |
| alerts | 2,179 |
| presence_states | 2 (both INSIDE) |
| courses | 0 |
| periods | 0 |
| attendance_records | 0 |
| attendance_overrides | 0 |
| alerts | 2,179 |

**Camera Configs:**
1. Classroom Exterior (10.10.150.178:554, user, doorway_exterior/exit, encrypted password)
2. Classroom Interior (10.10.149.188:554, user1, classroom_interior/entry, encrypted password)

**Encryption:** `EMBEDDING_ENCRYPTION_KEY` present in `.env` (32-byte base64url)

---

## 5. Environment & Runtime

| Component | Status |
|-----------|--------|
| Windows | Windows 10/11 (10.0.26300) |
| Python | 3.11.9 (venv active) |
| RAM | 8 GB total, ~4 GB used by GPU |
| GPU | NVIDIA RTX 5060 Laptop (8 GB VRAM, driver 617.42) |
| NVIDIA Driver | 617.42 |
| CUDA | 12.9 runtime available via nvidia-* wheels |
| ONNX Runtime GPU | 1.26.0 (CUDAExecutionProvider available) |
| InsightFace | 2.0 (buffalo_l models in `~/.insightface/models/buffalo_l/`) |
| Redis | Not running (binary at `%TEMP%\opencode\redis\redis-server.exe`) |
| Docker Desktop | **NOT INSTALLED** |
| WSL 2 | Present but not functional (`wsl --list` hangs) |
| Docker CLI | Not installed |
| Git | Not installed |
| ffmpeg | Not on PATH |
| psql | Not installed |

**Key Python Packages:**
- onnxruntime-gpu 1.26.0 (CUDAExecutionProvider ✓)
- insightface 2.0
- pywebview 6.2.1 (WebView2 backend available)
- pyinstaller 6.22.3
- portalocker 4.4.1
- celery 5.6.3 (threads pool on Windows)
- fastapi 0.143.1, uvicorn 0.32.0
- sqlalchemy 2.0.35, alembic 1.14.0

---

## 6. Deployment Artifacts (Existing)

| File | Status |
|------|--------|
| `dist/BirdEye.exe` | 370 MB (PyInstaller, console=False, WebView2) |
| `BirdEye.spec` | Updated (scripts/ removed from bundle) |
| `scripts/birdeye_launcher.py` | Process-safety patched (mutex, lifecycle, rollback) |
| `scripts/run_worker.ps1` / `.sh` | Threads pool, --beat on Linux |
| `scripts/run_beat.ps1` | Separate beat process for Windows |
| `docker-compose.yml` | 5 services (postgres, redis, api, inference, worker) |
| `Dockerfile` | python:3.11-slim-bookworm, onnxruntime-gpu, appuser |
| `.env` | Development defaults (sqlite, admin/admin, JWT_SECRET default) |

**Process-Safety Patch (Applied):**
- Windows mutex (`Global\BirdEyeLauncherMutex`) with abandonment handling
- ServiceManager: explicit process tracking, per-service 30s timeout, 180s overall deadline, rollback on failure
- atexit + SIGTERM/SIGINT handlers for cleanup
- Idempotent `stop_all()` (graceful → 10s force)
- `scripts/` removed from PyInstaller bundle (no recursive launch)

---

## 7. Reports Inventory

| Report | Location | Status |
|--------|----------|--------|
| 11_POWERSHELL_LOOP_INCIDENT.md | `PROJECT_RELEASE_REPORT/` | Root cause analysis |
| 12_PROCESS_SAFETY_PATCH.md | `PROJECT_RELEASE_REPORT/` | Patch documentation |
| 13_CONTROLLED_RUNTIME_VERIFICATION.md | `PROJECT_RELEASE_REPORT/` | 10/10 lifecycle tests passed |
| 14_RELEASE_PREFLIGHT.md | (this file) | In progress |

---

## 8. Data & Configuration Inventory

| Item | Location | Status |
|------|----------|--------|
| SQLite DB | `data/attendance.db` | 1.1 MB, preserved |
| Student photos | `data/students/1/*.jpeg`, `data/students/3/*.jpg` | Preserved |
| Camera passwords | Encrypted in `cameras.password_encrypted` | Fernet, never in logs |
| Encryption key | `.env` `EMBEDDING_ENCRYPTION_KEY` | 32-byte base64url, in `.env` |
| Camera passwords | Encrypted in `cameras.password_encrypted` | Fernet, write-only via UI |
| Admin credentials | `admin` / `admin` (default) | `password_source=env`, rotatable via API |
| Camera passwords | Not yet entered via UI | Must be entered via Cameras UI before live use |
| Sample video | `samples/videos/lecture.mp4` | **MISSING** (referenced by compose) |

**No backup files found.** Original SQLite must be backed up before any migration.

---

## 8. Docker / GPU Compatibility Assessment

| Requirement | Status |
|-------------|--------|
| Docker Desktop | **NOT INSTALLED** |
| WSL 2 | Present but non-functional (`wsl --list` hangs) |
| NVIDIA Container Toolkit | Not verified (Docker not installed) |
| GPU in Container | Requires `--gpus all` + NVIDIA Container Toolkit |
| `onnxruntime-gpu` in Container | Requires CUDA base image + `onnxruntime-gpu` wheel |
| `buffalo_l` Models | Must be downloaded in container (`scripts/download_models.py`) |
| Seed Video | `samples/videos/lecture.mp4` **MISSING** |

**GPU Inference Path Options:**
1. **Full Docker GPU** — Requires Docker Desktop + WSL 2 + NVIDIA Container Toolkit (not yet installed)
2. **Hybrid (Recommended for this host)** — Docker for postgres/redis/api/worker; native Windows inference service + worker (current working config)

---

## 9. Proposed Architecture

### Recommended: Hybrid Deployment (Windows Host + Docker for State/Queue)

| Component | Deployment | Rationale |
|-----------|------------|-----------|
| PostgreSQL | Docker (postgres:16-alpine) | Persistent, ACID, production-ready |
| Redis | Docker (redis:7-alpine) | Message bus, Pub/Sub, ephemeral |
| API | Docker (api service) | Stateless, horizontal scaling ready |
| Worker | Docker (worker service) | Camera pipelines, GPU reservation optional |
| Inference | **Native Windows** (`python -m app.inference.server`) | Proven GPU path, avoids container GPU complexity |
| Desktop Launcher | Native `BirdEye.exe` (PyInstaller + WebView2) | Native window, process safety, no browser |

**Rationale:** The native Windows inference path is already verified working (CUDAExecutionProvider, 14-16 ms/frame). Moving it to Docker adds complexity (NVIDIA Container Toolkit, WSL 2 GPU passthrough) without benefit on this single-GPU laptop. Docker handles state (PostgreSQL, Redis) and stateless services (API, Worker) cleanly.

**Worker Concurrency:** `CAMERA_SLOTS=8`, threads pool (10 workers = 8 camera slots + 2 control). Two cameras → 2 slots → concurrent pipelines verified.

---

## 10. Risks & Approval Requests

| Risk | Severity | Mitigation / Approval Needed |
|------|----------|------------------------------|
| Docker Desktop not installed | HIGH | Requires admin approval + install |
| WSL 2 non-functional | MEDIUM | May need WSL reset or Docker Desktop reinstall |
| Physical camera passwords not entered | HIGH | Must be entered via Camera UI before live use |
| Camera RTSP paths unconfirmed | HIGH | Dahua path `/cam/realmonitor?channel=1&subtype=0` works with Digest auth (verified) |
| Sample video missing | LOW | Provide or unset `SEED_VIDEO_PATH` |
| Default admin credentials | HIGH | Must rotate via `/auth/admin/users/1` after first login |
| GPU inference in Docker | MEDIUM | Hybrid path avoids this; native inference verified |
| OpenCode PowerShell leak | EXTERNAL | Workaround: `MaxProcesses=20` registry key (admin) |
| No Git | LOW | Install Git for version control |

**Approvals Requested:**
1. Install Docker Desktop (official installer, admin UAC)
2. Rotate admin password via API after first login
3. Enter camera passwords via Camera UI (encrypted, never in logs)
4. Provide/resolve sample video path or unset `SEED_VIDEO_PATH`

---

## 11. Stage 1 Change Plan (Proposed)

| Task | Description | Files Affected |
|------|-------------|----------------|
| 1. Frontend API config | Verify API base URL, token handling, WS reconnect | `app/static/app.js` |
| 2. Camera UI hardening | Verify password encryption, test/preview flow | `app/static/app.js`, `app/api/cameras.py` |
| 3. Compose validation | Verify `docker-compose.yml` syntax, healthchecks | `docker-compose.yml`, `Dockerfile` |
| 4. Desktop packaging | Rebuild `BirdEye.exe` with patched launcher | `BirdEye.spec`, `scripts/birdeye_launcher.py` |
| 5. Compose preparation | Verify GPU reservations, volume mounts, healthchecks | `docker-compose.yml`, `Dockerfile` |
| 6. Resource budget | Set conservative limits for RAM/GPU | `.env`, `docker-compose.yml` |

**No destructive operations. No database migration. No service startup in this stage.**

---

## 12. Approval Request

**Requesting approval to proceed to Stage 1 (Application Integration & Deployment Preparation).**

Key actions in Stage 1:
- Frontend API integration review (no build yet)
- Docker Compose validation (static only)
- Desktop packaging design finalization (WebView2 confirmed available)
- Resource budgeting for Docker memory limits

**No Docker installation, no service startup, no build, no database migration in Stage 1.**

---

*Report generated: 2026-10-20*  
*Stage 0 complete. Awaiting approval for Stage 1.*