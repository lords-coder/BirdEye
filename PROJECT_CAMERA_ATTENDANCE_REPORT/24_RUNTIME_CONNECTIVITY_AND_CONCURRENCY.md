# 24 — Runtime Connectivity, Dual-Camera Concurrency & Recognition Boundaries

**Date:** 2026-10-19  
**Environment:** Windows host, Python 3.11.9 (venv), no Docker/WSL distro usable here.  
**Suite:** `python -m pytest` → **272 passed, 0 failed** (68.72 s).

This report separates three things that earlier reports sometimes blurred:

1. **Camera configuration** — registering a stream in the secure UI, credentials
   encrypted at rest, never echoed.
2. **Authenticated live-stream verification** — proving the browser gets frames
   from the *running pipeline* (never a second RTSP capture), over header-auth
   MJPEG/snapshot.
3. **Actual recognition** — the GPU inference service matching faces against an
   enrolled gallery.  Correctness of the pipeline plumbing is verified here;
   real-CCTV accuracy (TAR/FAR) is NOT measured and NOT claimed.

---

## 1. How camera tasks are dispatched, consumed, monitored, stopped

| Stage | Mechanism | Where |
|-------|-----------|-------|
| Dispatch | `POST /cameras/{id}/start` claims a free Redis slot (NX + stale-reclaim), writes `state=starting` + heartbeat *now*, enqueues `camera.run` onto `camera_slot_N` | `app/api/cameras.py` |
| Consume | Celery worker bound to `control,jobs,camera_slot_0..7`, `prefetch=1`, one long task per slot | `app/workers/celery_app.py`, `app/tasks.py` |
| Run | `run_camera_pipeline(camera_id, slot)`: FrameReader thread → bounded drop-oldest queue → batched Redis RPC to shared GPU inference service → cosine match → presence/attendance | `app/pipeline/camera_task.py` |
| Monitor | `GET /cameras/{id}/live-status` (header auth): `state`, `running`, `has_frame`, `fps`, `infer_ok`, `infer_ms`, `faces`, `error`, `viewers` | `app/api/cameras.py` |
| Stream | `GET /cameras/{id}/live.mjpeg` serves the *shared* Redis publication (N viewers, 0 captures); 409 when stopped, 503 when running-but-empty | `app/api/cameras.py` |
| Stop | `POST /cameras/{id}/stop` sets stop flag; `force=true` revokes the Celery task and clears `state/stop/heartbeat/task/slot`; pipeline `finally` joins reader + deletes keys | `app/api/cameras.py`, `app/pipeline/camera_task.py` |
| Reaper | Beat `jobs.reap_dead_cameras` (every 60 s) reclaims slots whose heartbeat expired | `app/tasks.py` |

**Slot model:** `CAMERA_SLOTS` dedicated Celery queues (`camera_slot_0 .. N-1`, default 8). A camera holds exactly one slot for its whole lifetime.

---

## 2. Concurrency acceptance gate (new) — `scripts/gate_concurrency.py`

Runs against a live local stack (API + Redis + worker + inference) using **file-based**
camera sources, so no physical camera is required. It tests the
dispatch/consume/monitor/stop machinery, not RTSP connectivity.

**`scripts/gate_concurrency.py` — 12/12 checks PASSED:**

| # | Check | Result |
|---|-------|--------|
| 1 | Both `/start` accepted (HTTP 200) | PASS |
| 2 | Camera A reaches `running` on a dedicated slot | PASS (slot 0) |
| 3 | Camera B reaches `running` on a dedicated slot | PASS (slot 1) |
| 4 | Two DISTINCT slots (two queues, two worker threads) | PASS (0 ≠ 1) |
| 5 | Both running at the same time (concurrency) | PASS |
| 6 | Stop A accepted | PASS |
| 7 | Camera A stopped | PASS |
| 8 | Camera B **still running** while A stopped (independence) | PASS |
| 9 | Stop B accepted | PASS |
| 10 | Camera B stopped | PASS |
| 11 | Third camera start accepted | PASS |
| 12 | Freed slot reclaimed by third camera | PASS (slot 0) |

**Conclusion:** the Windows worker **does not** use the solo pool —
`scripts/run_worker.ps1` defaults to the `threads` pool (`CELERY_POOL`
overridable), concurrency = `CAMERA_SLOTS + 2`, `prefetch-multiplier=1`. The
shared module-level `_CONFIRM_BUFFER` is guarded by `_CONFIRM_LOCK` so concurrent
camera threads in one worker process cannot race. Two camera pipelines operate
concurrently and independently; stopping one never stops the other.

---

## 3. Camera connectivity diagnostics (task 5)

`app/services/camera_test.py` classifies every probe into exactly one status:

| Status | Meaning | Returned when |
|--------|---------|---------------|
| `connected` | stream reachable, one frame decoded | ffmpeg/OpenCV returns rc=0 / decodes a frame |
| `auth_failed` | camera rejected credentials (HTTP 401) | ffmpeg stderr contains 401/unauthorized |
| `unreachable` | host/port refused, no route, or DNS failure | TCP refused / network unreachable / no route |
| `timeout` | no response within timeout | ffmpeg/OpenCV read timeout |
| `unsupported` | server answered but path/method not supported | 404/405/method-not-allowed |
| `no_frames` | stream opened but delivered no decodable frame | `cap.read()` returns False |
| `error` | anything else (honest "check credentials / path" hint) | OpenCV `isOpened()==False` after TCP ok |

**Never claims authentication failure unless ffmpeg reported 401.** OpenCV cannot
prove an auth failure, so it reports the honest `error` with a "check username,
password and RTSP stream path" hint. All diagnostic text passes through
`camera_test.redact()` which strips `user:pass@` — credentials never reach the
response, database, or logs.

Verified by new `tests/test_camera_connectivity.py` (5 tests, all pass):
credential redaction, refused-port classification, unreachable-host classification
without leaking the password, missing-file → `unreachable`, empty source →
`error`.

**Inference unavailable** (the 5th category) is surfaced separately on
`GET /cameras/{id}/live-status` as `infer_ok=false` + an explicit `error`
message (e.g. `"inference unavailable (3 consecutive failures)"`), and the
pipeline raises a high-severity `pipeline_error` alert after 3 consecutive RPC
failures while recognition logs stay flat (no false "no face" rows). Verified by
`tests/test_live_monitor.py::test_live_status_reports_inference_outage` and the
live outage drill in §4.

---

## 4. Live inference outage drill (control-plane verification)

Ran against the live stack with the shared GPU inference service stopped mid-run:

| Observation | Result |
|-------------|--------|
| `live-status` flipped to `infer_ok=false` with `"inference unavailable (N consecutive failures)"` | PASS (never silently reports faces=0 as "empty room") |
| `pipeline_error` alert raised (severity=high) after 3 failures | PASS |
| Recognition logs did **not** grow during outage (+0 rows) | PASS — failure ≠ ordinary no-face result |
| Both pipelines stayed `running` (no crash, no phantom state) | PASS |
| Service restarted → inference recovered **on its own** (CUDA provider restored) | PASS |
| No duplicate pipelines / no slot collision after recovery | PASS (slots 0 & 1) |

---

## 5. Camera configuration vs authenticated live-stream vs recognition (task 10)

These are deliberately kept separate; do not conflate them.

### 5.1 Camera configuration (secure UI)
- Admin registers/edits a camera via `POST/PUT /cameras` with `host`, `port`,
  `username`, `password` (write-only) and `stream_path`.
- The password is Fernet-encrypted at rest (`app/crypto.py`, `EMBEDDING_ENCRYPTION_KEY`).
- Responses expose only `has_password` + a credential-redacted `display_url` /
  `rtsp_url`. The raw password is **never** in any API response, log, or report.
- Verified by `tests/test_api.py::test_camera_never_leaks_the_stream_password`.
- Connectivity is proven through `POST /cameras/{id}/test` (classified,
  credential-redacted) — **not** by opening the stream in the browser.

### 5.2 Authenticated live-stream verification
- `GET /cameras/{id}/live.mjpeg` (header auth only — no `?token=` in URLs) and
  `GET /cameras/{id}/snapshot` serve the **shared Redis publication** from the
  running pipeline. They open **no camera connection** of their own.
- 409 when the pipeline is stopped; 503 when running-but-empty (never falls
  through to a competing RTSP probe).
- N viewers share ONE published frame (`iter_live_parts`, viewer ref-counting).
- Parts carry `X-Camera-Id` and `X-Frame-Ts` so the UI detects stale frames /
  camera switches.

### 5.3 Actual recognition (boundary of what is claimed)
- The shared GPU inference service (`python -m app.inference.server`) loads
  InsightFace `buffalo_l` exactly once (SCRFD detect + ArcFace embedding), runs
  on `CUDAExecutionProvider`, and is shared by all camera workers over Redis RPC.
- Measured on this host: **~14–16 ms/frame steady state** on GPU (RTX 5060,
  8 GB) after cuDNN warmup (~45× faster than the 696 ms CPU path); ~32 ms EMA
  end-to-end under load with both cameras running.
- Identity matching against an enrolled student (Karan Agrawal, 3 consented
  reference photos) was verified end-to-end through the real RPC + matching
  index: 3/3 photos detected, correct match (score 1.00), unrelated vector →
  `UNKNOWN` (score 0.047, below the 0.40 threshold).
- **NOT claimed:** real-CCTV recognition accuracy (TAR/FAR). That requires
  physical-camera enrollment + recognition walkthrough against live subjects,
  which cannot be completed here because the camera hosts are unreachable from
  this environment (§7).

---

## 6. Admin credential change (task 7)

The supported mechanism already exists and is tested — no silent reset:

- First login uses the seeded super admin from `ADMIN_USERNAME`/`ADMIN_PASSWORD`
  (`admin`/`admin`), bootstrapped idempotently by `ensure_seed_superuser` with
  `password_source="env"`.
- The super admin changes any password via `PATCH /auth/admin/users/{id}`
  (super admin only; cannot demote/disable the last active super admin).
- Setting a password through the API flips `password_source` to `"manual"`, so
  `.env` can **never** override it again — no silent reset.
- Verified by `tests/test_auth_users.py::test_super_admin_can_change_own_password`
  (old `admin`/`admin` → 401, new password → 200) and
  `test_super_admin_changes_username_and_password`.

Workflow for a shared deployment:
1. Log in as `admin`/`admin` (seeded).
2. `PATCH /auth/admin/users/{id}` with a strong new password.
3. Rotate `ADMIN_PASSWORD` in `.env` (startup fails in production while it is
   still `"admin"`).
4. Optionally create operator accounts via `/auth/register` → super admin
   approves → they log in with their own credentials.

---

## 7. Redis setup — supported vs temporary (task 8)

### Supported (persistent) — Docker Compose
```bash
cp .env.example .env && python scripts/generate_key.py   # put key in .env
docker compose up -d   # postgres, redis, api, worker, inference
```
- `redis:7-alpine` with `--appendonly yes` and the named `redis_data` volume
  (persistent across restarts).
- `worker` runs `scripts/run_worker.sh` with `--beat` (reap-dead-cameras,
  check-period-deviations, close-stale-sessions all scheduled).
- `inference` runs `scripts/download_models.py` then `python -m app.inference.server`
  with the NVIDIA GPU reservation.

### Windows dev fallback (already supported, no Docker)
```powershell
.\.venv\Scripts\python.exe scripts\setup_dev.ps1   # venv, deps, .env, key, migration, weights, tests
# Terminal 1: Redis
redis-server --port 6379 --appendonly yes
# Terminal 2: shared GPU inference service
.\.venv\Scripts\python.exe -m app.inference.server
# Terminal 3: Celery worker (threads pool on Windows)
powershell -File scripts\run_worker.ps1
# Terminal 4: API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### What was on this machine (NOT a deployment)
A `redis-server.exe` was installed outside the project into
`%TEMP%\opencode\redis\` purely to run the local acceptance drill above. It has
been stopped. **Do not treat this as a production deployment** — use Docker
Compose (above) or install Redis as a proper Windows service for anything
beyond a one-off test.

---

## 8. Exact startup commands (this host)

Windows, venv active (`$env:PYTHONPATH="."`):

```powershell
# 0. one-time dev bootstrap (idempotent)
.\.venv\Scripts\python.exe scripts\setup_dev.ps1

# 1. Redis bus (dev only; use docker compose for persistence)
redis-server --port 6379 --appendonly yes --dir %TEMP%\opencode\redis

# 2. shared GPU inference service (loads buffalo_l on CUDA)
.\.venv\Scripts\python.exe -m app.inference.server

# 3. Celery worker - threads pool on Windows, --beat NOT supported in-process here
powershell -File scripts\run_worker.ps1
# (on Linux/Docker the compose `worker` service passes --beat; or run a
#  separate `celery -A app.workers.celery_app:celery_app beat`)

# 4. API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 5. concurrency acceptance gate (against the live stack)
.\.venv\Scripts\python.exe scripts\gate_concurrency.py   # 12/12 expected

# 6. regression suite
.\.venv\Scripts\python.exe -m pytest                    # 272 passed, 0 failed
```

---

## 9. Remaining environment blockers (honest, not invented)

| Blocker | Detail | Required action |
|---------|--------|-----------------|
| Physical camera hosts unreachable | `10.10.150.178:554` / `10.10.149.188:554` cannot be reached from this environment | Physical-camera enrollment + recognition walkthrough (report §2.13 steps 5–10) cannot be completed here; do not invent connectivity |
| RTSP passwords not entered via UI | Neither camera has a password stored through the secure UI; authenticated stream never opened | Enter both passwords via Cameras → edit → Test → Save; rotate post-acceptance |
| No Docker on Windows host | `docker`/`psql`/`ffmpeg`/`git` absent on PATH; WSL distro not responsive | Validate the Compose stack on a Docker host (report 23 §9): `docker compose up --build`, then exec inference/model/api/migrations/worker |
| WSL present but unverified | `wsl.exe` on PATH but `wsl --list` did not return in this session | Install a WSL distro + Docker Desktop WSL backend to run the Linux path |
| GPU inference accuracy unmeasured | CUDA provider + models verified working; matching verified on 3 enrolled photos | TAR/FAR requires `scripts/check_accuracy.py` on real gallery + institutional sign-off on thresholds |
| Institutional sign-off | `recognition_threshold` (0.40), `required_minutes` (30), grace windows are engineering defaults | Obtain SRM Haryana sign-off |
| Seed video absent | `./samples/videos/lecture.mp4` referenced by compose is not in the repo | Provide it or leave `SEED_VIDEO_PATH` unset |

---

## 10. Test results

| File | Result |
|------|--------|
| Full suite (`pytest`) | 272 passed, 0 failed (68.72 s) |
| `tests/test_camera_connectivity.py` (new) | 5 passed |
| `tests/test_api.py` (+1 leakage test) | included above |
| `scripts/gate_concurrency.py` (live) | 12/12 passed |

No existing test was weakened or deleted. No database was reset or truncated.
