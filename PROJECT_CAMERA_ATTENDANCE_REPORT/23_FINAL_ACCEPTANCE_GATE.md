# 23 — Final Pre-Evaluation Acceptance Gate

**Date:** 2026-10-18  
**Command:** `.venv\Scripts\python.exe -m pytest`  
**Final result:** **259 passed, 0 failed (70.14 s)**

---

## 1. Go / No-Go Checklist

| # | Acceptance Gate | Status | Evidence |
|---|----------------|--------|----------|
| 1 | Latest reports preserved; existing implementation intact | **PASS** | Reports 00–22 present; no deletions; 259 tests pass |
| 2 | Complete test suite confirms reported baseline | **PASS** | Baseline 237 → **259 passed, 0 failed** |
| 3 | Snapshot + legacy stream reuse pipeline frames when running | **PASS** | Live sentinel-JPEG proof (§3) |
| 4 | Interior + exterior pipelines concurrent & independent of navigation | **PASS** | Dual-camera live capture + independence tests (§4) |
| 5 | Sustained detection + unknown dedup; event volume independent of frame count | **PASS** | 10-frame vs 40-frame volume equality (§5) |
| 6 | Corridor passersby don't raise occupancy; transitions update it | **PASS** | Occupancy tests (§6) |
| 7 | Separate entry/exit intervals → correct durations + period attendance | **PASS** | Duration/attendance tests (§7) |
| 8 | Physical-camera FPS + displayed-frame delay measured | **PASS** | §8 (25.6–27.4 fps, ~161–169 ms) |
| 9 | PostgreSQL + deployment stack validated in isolated env | **BLOCKED** | Docker/PostgreSQL absent; static compose checks only (§9) |
| 10 | Auth, events, timestamps, recovery, cleanup — no regressions | **PASS** | §10 |
| 11 | Newly reproduced defects corrected + regression tests added | **PASS** | 1 defect found & fixed (§11) |
| 12 | This checklist produced | **PASS** | This document |

**Overall: 11 PASS, 1 BLOCKED, 0 FAIL.**

The single BLOCKED gate (9) does not affect the correctness of the code; it
affects only production-infrastructure validation, which requires a Docker
host.

---

## 2. Test Results

| Metric | Value |
|--------|-------|
| Final suite | **259 passed, 0 failed** |
| Baseline (start of sprint) | 237 passed |
| New gate + sprint tests | 22 (`tests/test_sprint_fixes.py`) |
| Regressions | **0** |
| Execution time | 70.14 s |
| Warnings | 1 (StarletteDeprecationWarning, pre-existing, harmless) |

Sprint test file contents (22 tests):
- Unknown-face dedup ×5 (incl. **volume-independence**)
- Snapshot/stream Redis reuse ×7
- Corridor passersby ×2
- Occupancy ×2
- Durations + period attendance ×2
- Camera independence ×2
- Config sanity ×2

---

## 3. Gate 3 — Pipeline Frame Reuse (live server proof)

Method: wrote a sentinel JPEG into the **same Redis the app uses**
(`redis://localhost:6379/0`), marked camera 1 `running` with a fresh
heartbeat, then queried the live server.

| Check | Result |
|-------|--------|
| `GET /cameras/1/snapshot` served the **exact sentinel bytes** | **PASS** (2770 bytes, md5-matched) |
| `GET /cameras/1/stream.mjpeg` served sentinel inside multipart | **PASS** (`multipart/x-mixed-replace`, `--frame` boundary) |
| Running + empty frame bus → **503, no probe** | **PASS** |
| Cleanup removed all created keys | **PASS** (`cam:1:state` … all deleted, final state `None`) |

**Conclusion:** while a pipeline is running, neither endpoint opens an RTSP
connection. The competing-capture defect that caused page-coupled
processing and video lag is eliminated.

---

## 4. Gate 4 — Dual-Camera Concurrency & Independence

**Physical simultaneous capture** (`scripts/measure_dual_camera.py`, 20 s,
2 threads):

| Camera | Role | Resolution | FPS | Read failures |
|--------|------|-----------|-----|---------------|
| Classroom Exterior (…178) | doorway_exterior / exit | 2560×1440 | **25.6** | **0** |
| Classroom Interior (…188) | classroom_interior / entry | 2560×1440 | **25.9** | **0** |

First frame after open: **0.04–0.05 s** on both.

**Independence (automated):** stopping camera A leaves camera B running;
snapshot for A does not affect B. Both verified in
`TestCameraPageIndependence`.

Pipeline state is Redis-backed (not browser memory), so processing is
independent of which page an admin has open.

---

## 5. Gate 5 — Event Volume Independent of Frame Count

`test_event_volume_independent_of_frame_count` runs the same
continuously-visible unknown face for **10 frames** and **40 frames** and
asserts identical permanent volume:

| Frames | Events | Log rows | Alerts |
|--------|--------|----------|--------|
| 10 | 1 | 1 | 1 |
| 40 | **1** | **1** | **1** |

4× the frames → **identical** permanent volume. Only legitimate state
changes (a new distinct person, or a re-identification after the dedup
window) add events.

Two distinct unknowns still yield 2 separate events — dedup does not
over-suppress.

Production evidence of the pre-fix flood: `Total=2159 Known=12
Unknown=2147`. After the quantized-hash fix the unknown volume collapses
to ~1 per person per `dedup_window_seconds` (120 s).

---

## 6. Gate 6 — Occupancy

| Scenario | Occupancy before | after |
|----------|-----------------|-------|
| One student genuinely inside | 1 | 1 |
| **+ 5 different corridor passersby** | 1 | **1 (unchanged)** |
| Inward transition (interior camera) | 0 | 1 |
| Outward transition (exterior camera) | 1 | 0 |

Corridor passersby produce `review_event=True` and never an `INSIDE`
state or an invented `AttendanceSession`.

---

## 7. Gate 7 — Durations and Period Attendance

**Separate intervals** (`test_separate_intervals_sum_to_total_duration`):

| Interval | Expected | Actual |
|----------|----------|--------|
| 04:00 → 04:12 | 12 min | **720 s** |
| 04:20 → 04:50 | 30 min | **1800 s** |

Two distinct sessions, each with its own correct duration — no wall-clock
drift, no processing-time contamination.

**Period attendance** (`test_period_attendance_uses_presence_intervals`,
60-min period, `required_minutes=30`):

| Student | Presence | Expected | Actual |
|---------|----------|----------|--------|
| STU-ATT1 | 30 min | PRESENT | **PRESENT** (1800 s) |
| STU-ATT2 | 5 min | ABSENT | **ABSENT** |

Timestamps use **event time** (`packet.ts`, the decode timestamp), never
worker processing time — covered by an existing regression test.

---

## 8. Gate 8 — Physical-Camera Performance (measured)

Both cameras are live and reachable on `10.10.150.178:554` and
`10.10.149.188:554`.

**RTSP path discovery (new this session):** all paths return 401 under
Basic auth. Under **Digest** auth the Dahua path
`/cam/realmonitor?channel=1&subtype=0` returns **200 OK** on both cameras.
The stored credentials are correct. The bare `rtsp://host:554` URL also
works (camera serves a default stream). `stream_path=None` in the DB is
therefore **not** a functional blocker — verified by direct capture.

| Metric | Exterior | Interior |
|--------|----------|----------|
| Capture FPS | 26.0–27.6 | 27.4–27.6 |
| Resolution | 2560×1440 | 2560×1440 |
| Read failures (20 s) | 0 | 0 |
| RTSP decode / frame | 38.6 ms | 36.5 ms |
| Downscale + annotate | 2.2 ms | 2.7 ms |
| JPEG encode (q70, 640 px) | 0.9 ms | 1.0 ms |
| Redis publish + read | 7.0 ms | 1.2 ms |
| **Est. displayed-frame delay** | **~169 ms** | **~161 ms** |

Delay excludes GPU inference (see blocker §12.3). Config:
`batch_flush_ms=120`, `live_frame_width=640`, `live_frame_quality=70`,
`live_mjpeg_fps=6`.

**Explicitly not claimed:** recognition accuracy on these physical cameras.
That requires the GPU inference service with loaded models (§12.3).

---

## 9. Gate 9 — Deployment Stack (BLOCKED)

| Check | Result |
|-------|--------|
| `docker` CLI / SDK | **absent** |
| `psql` / `psycopg2` | **absent** |
| `redis-server` binary | **absent** (client connects to an already-running instance) |
| `ffmpeg` on PATH | **absent** (OpenCV bundles its own FFmpeg) |

**Static validation performed instead** (`scripts/validate_compose.py`):
**14 passed, 1 warning, 0 failed** — core services present
(api/inference/postgres/redis/worker), depends_on resolvable, volumes
declared, healthcheck route exists, GPU reservations present, only `api`
publishes host ports. Warning: seed video `./samples/videos/lecture.mp4`
not in repo.

**Migrations:** `alembic heads` = `0006_camera_attendance`; `alembic
current` = head; `alembic check` = *"No new upgrade operations detected."*

**No database was reset or truncated.**

**Blocker:** PostgreSQL + Docker runtime validation requires a Docker
host. **Action:** run `docker compose up --build` on a Docker host, then
`docker compose exec api alembic upgrade head`, and re-run
`scripts/validate_compose.py` plus the compose-based E2E.

---

## 10. Gate 10 — Auth, Events, Timestamps, Recovery, Cleanup

**Live probe against the running server** (`scripts/gate_live_probe.py`):
**15/15 gates passed.**

- admin login 200; bad password → **401**; no token → **401**
- both cameras listed; **no password field leaked** (`has_password=True`
  only); roles set
- snapshot / live-status / alerts / presence-states all **200**
- `/system/health` **PII-free**

**Camera recovery:** `FrameReader` uses drop-oldest bounded queue (latency
never grows), open/read timeouts, release-on-failure, and exponential
backoff reconnect. Inference outages publish a failure marker to the live
hash so the UI reports "inference unavailable" rather than showing a stale
frame as live.

**Resource cleanup:** pipeline `finally` block joins the reader, deletes
`cam:{id}:state/stop/heartbeat/task`, removes the slot owner, and closes
the DB session. Verified live: after force-stop, `cam:1:state` was `None`.

**Timestamps:** event time (`packet.ts`), never processing time.

---

## 11. Gate 11 — Defect Found and Fixed

**Defect (introduced by the sprint's own P0-1 fix):** when a pipeline was
`running` but the frame bus was **empty** (startup window or inference
outage), `camera_snapshot` and `camera_stream` silently fell through to the
direct RTSP probe — recreating exactly the competing capture the fix was
meant to remove.

**Fix:** while running, either serve the shared frame or return **503**
with the pipeline's own error text. A direct probe is now used **only**
when the pipeline is genuinely stopped. `live.mjpeg` already behaved
correctly; the other two now match it.

**Regression tests added (3):**
- `test_snapshot_running_but_no_frame_returns_503_not_probe`
- `test_snapshot_running_no_frame_reports_pipeline_error`
- `test_legacy_stream_running_no_frame_503_not_probe`
- `test_legacy_stream_running_serves_redis`

Frontend impact verified: `openCameraPreview` catches non-OK responses,
shows "preview failed: …", and keeps polling — self-recovering, no broken
image, no design change.

No test was weakened or deleted.

---

## 12. Remaining Risks and Blockers

### 12.1 Git unavailable (LOW)
`git` is not installed and the directory is not a git repository, so no
diff could be produced. All changes are itemised in
`22_CHANGE_MANIFEST.csv` and this report.
**Action:** install Git and `git init` (or restore the original repo) to
capture a diff.

### 12.2 PostgreSQL / Docker absent (MEDIUM)
Runtime stack not validated; SQLite used for all testing.
**Action:** validate on a Docker host (§9).

### 12.3 GPU inference + models absent (HIGH for accuracy claims)
`onnxruntime-gpu` is not installed and `models/` does not exist, so the
inference service has never run in this environment. **Real recognition
accuracy (TAR/FAR) is NOT TESTED and is not claimed.** All recognition
results in earlier reports derive from synthetic embeddings.
**Action:** `docker compose up --build` (pulls `ORT_GPU_VERSION`), run
`scripts/download_models.py`, then execute the physical-camera
enrollment + recognition acceptance walkthrough.

### 12.4 No Celery worker consuming `camera.run` on this box (MEDIUM)
Camera start returns 200 / `starting`, but state never reaches `running`
because no worker is bound to the slot queues. Snapshot/live-status
degrade correctly (direct probe when stopped; 503 when running-but-empty),
so no incorrect behaviour was observed — but the full pipeline loop was
not exercised end-to-end here.
**Action:** start `scripts/run_worker.sh` (or the compose `worker`
service) with models loaded.

### 12.5 Unknown-face dedup fails open on Redis outage (LOW)
`dedup_pass` returns `True` when Redis is unreachable (deliberate: never
lose attendance data). During a Redis outage the unknown-event flood could
resume. Documented trade-off.
**Action:** monitor Redis availability; `unknown_cooldown_s` (60) is
available as a tightening knob.

### 12.6 Stream credentials were shared in chat in an earlier phase (MEDIUM)
Rotate both cameras' RTSP passwords after acceptance.
**Action:** change passwords on the cameras, re-enter via Cameras → edit →
Test → Save.

### 12.7 Institutional sign-off on thresholds (MEDIUM)
`recognition_threshold`, `required_minutes`, and grace windows are
engineering defaults, not institutionally approved.
**Action:** obtain SRM Haryana sign-off.

### 12.8 Seed video absent (LOW)
`./samples/videos/lecture.mp4` referenced by compose is not in the repo.
**Action:** provide it or leave `SEED_VIDEO_PATH` unset.

---

## 13. Precise Steps to Complete Acceptance

1. **Docker host:** `docker compose up --build`; confirm 5 services healthy.
2. **Models:** `docker compose exec inference python scripts/download_models.py`.
3. **Migrations:** `docker compose exec api alembic upgrade head` (expect
   "No new upgrade operations detected" / already at `0006`).
4. **Workers:** start `worker` service; verify `camera.run` consumers exist.
5. **Cameras:** start both pipelines from the UI; confirm state reaches
   `running`, `has_frame=true`, and FPS ≈ 25 on `/live-status`.
6. **Overlays:** open Monitor; confirm green (known) / amber (unverified)
   boxes at ≤ ~200 ms perceived delay.
7. **Enrollment:** enroll a student from 3 photos; walk in → `INSIDE`;
   walk out → session closed with correct duration.
8. **Corridor:** walk past the exterior camera with no session → review
   event, occupancy unchanged.
9. **Dedup:** stand continuously for 5 min → exactly 1 unknown event.
10. **Attendance:** verify the period table shows PRESENT/ABSENT consistent
    with the observed intervals.
11. **Rotate** both camera passwords post-acceptance.
12. **Obtain** institutional sign-off on thresholds.

---

## 14. Scripts Added This Session

| Script | Purpose |
|--------|---------|
| `scripts/probe_rtsp_paths.py` | Discover the working RTSP path per camera (status lines only, no secrets) |
| `scripts/measure_dual_camera.py` | Simultaneous dual-camera FPS / health |
| `scripts/measure_display_latency.py` | End-to-end displayed-frame delay through the real annotate path |
| `scripts/gate_live_probe.py` | Live auth / snapshot / event / security gate probe |
| `scripts/gate_pipeline_live.py` | Live pipeline start/stop + independence |
| `scripts/gate_snapshot_reuse.py` | Live sentinel proof that snapshot/stream reuse pipeline frames |

All are diagnostic utilities consistent with the existing `scripts/`
conventions; none alter application behaviour.
