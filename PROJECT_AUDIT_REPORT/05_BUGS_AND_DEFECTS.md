# 05 — Bugs and Defects

Severity: **S1** = feature/endpoint dead or deployment impossible · **S2** = wrong behavior in a reachable path · **S3** = edge-case/latent defect · **S4** = cosmetic/hygiene.

Every "CONFIRMED" defect was established by direct code reading with file:line evidence. **No runtime reproduction was possible** (no Python interpreter in this environment) — where the defect is a deterministic code-level error (type confusion, missing file, wrong signature), static evidence is sufficient and marked CONFIRMED. Items whose failure depends on runtime conditions are marked SUSPECTED or UNVERIFIED.

## A. Confirmed defects

### BUG-01 (S1) — `POST /students/{id}/photos` can never succeed
- **Where:** `app/services/enrollment.py:491-512`, specifically `:495` `current = list(student.embeddings or [])`
- **Defect:** `student.embeddings` is the **encrypted base64 ciphertext string** (decrypt happens later at `:505`). `list(str)` iterates characters, so `len(current)` equals the ciphertext length (hundreds), and the guard `len(current) + len(photos) > settings.max_reference_photos` (`:496`) is always true → `EnrollmentError` for every student who has any embeddings. The `start_index` passed to `_save_photos_append` (`:502`) is likewise wrong.
- **Expected:** count the decrypted embedding list (`len(student.embedding_list)`).
- **Actual:** every request fails with a photo-count error; the API route `app/api/students.py:180-202` and the frontend "+ photos" button (`app.js:366`) are dead.
- **Impact:** students cannot gain reference photos after enrollment; the feature advertised in README §7 (`POST /students/{id}/photos`) is nonfunctional.
- **Fix direction:** compute `current` from `decrypt_embeddings(student.embeddings)` (or `student.embedding_list`) before the count check.
- **Verification after fix:** add a regression test that enrolls a student (3 photos) then successfully adds a 4th via the API; expect 200 and `embedding_count == 4`.

### BUG-02 (S1) — Docker deployment impossible: missing `Dockerfile` and `scripts/run_worker.sh`
- **Where:** `docker-compose.yml:18` (`build: context: .` requires `./Dockerfile` — absent), `docker-compose.yml:106` (`command: ["bash", "scripts/run_worker.sh"]` — file absent; verified against full recursive listing)
- **Impact:** `docker compose up` fails at build for every service; even with a Dockerfile, the worker container exits immediately. README §1's claim "Dockerfile + docker-compose.yml are provided" is false. The documented full-stack path (README §11) is impossible.
- **Also missing (referenced by docs):** `.env.example` (README:371), `scripts/setup_dev.ps1/.sh` (README:76,84,355; CONTRIBUTING:6,79), `scripts/setup_gpu.ps1/.sh` (requirements.txt:17), `Makefile` (README:98-99), `requirements-dev.txt` (README:90, CONTRIBUTING implied), entire `eval/` directory incl. `ROADMAP.md` (README:10,245; CONTRIBUTING:39,88), `data/README.md` (README:196), `samples/videos/lecture.mp4` (compose:70,111; `config.py:136`).
- **Verification after fix:** `docker compose build && docker compose up -d` healthy on all 5 services; worker consumes `camera_slot_*`.

### BUG-03 (S1) — `judge_demo.py` gallery setup crashes with `TypeError`
- **Where:** `scripts/judge_demo.py:244` calls `setup_student_database.main(argv)`; `scripts/setup_student_database.py:349` defines `def main() -> int:` (no parameter)
- **Impact:** deterministic `TypeError: main() takes 0 positional arguments but 1 was given` when the judge demo reaches its STEP 2 (gallery build) with real code. Tests don't catch it because `tests/test_judge_demo.py` monkeypatches `run_enrollment`/setup functions.
- **Verification after fix:** `python scripts/judge_demo.py --help` runs; a dry run reaches STEP 2 without TypeError.

### BUG-04 (S1/S2) — Migration/model type mismatch on `students.embeddings`
- **Where:** `alembic/versions/0001_initial.py:29-34` creates `embeddings` as `sa.JSON(), server_default="[]"`; `app/models.py:42-44` maps it as `String, server_default=""`.
- **Impact:** any database created by `alembic upgrade head` (the documented/compose path) has a JSON column that the ORM accesses as plain String ciphertext. On PostgreSQL this risks cast errors or silent misbehavior on read/write; the server defaults also disagree (`'[]'` vs `''`). Tests never see it because `tests/conftest.py:107-108` uses `Base.metadata.create_all` (model-driven), bypassing migrations entirely.
- **Confidence:** mismatch CONFIRMED; exact PostgreSQL runtime symptom UNVERIFIED (no DB here).
- **Fix direction:** new migration `0005` altering the column to TEXT (SQLite batch mode already enabled in `env.py:49`), and a test that runs migrations against a scratch DB.

### BUG-05 (S2) — Infinite WebSocket reconnect loop after logout
- **Where:** `app/static/app.js:57` `getToken()` returns `""` when unset; `app.js:578` guard is `if (getToken() !== null)` — always true
- **Actual:** after `logout()` (which closes the socket at `:63`), the 4-second retry timer keeps firing; each attempt is rejected (4401 pre-accept when auth required) and reschedules itself forever on the login page.
- **Verification:** log in, open Live, log out, observe network tab: `/ws/live-feed` attempt every ~4 s indefinitely.

### BUG-06 (S2) — `logout()` leaks the live-poll timer and the WS retry timer
- **Where:** `app/static/app.js:60-67` clears neither `liveTimer` (declared `:240`, cleared only inside `showSection` `:247`) nor `wsRetry`
- **Actual:** logging out from the Live tab leaves a 5-second `GET /attendance/live` poll running forever; each 401 re-invokes `logout()` and `showAuthCard("login")`, yanking the user back to the login card while they type on the Register/Forgot cards. A pending `wsRetry` can also spawn a duplicate socket (shared `ws` variable, `onerror` closes the wrong socket at `:580`).
- **Verification:** as BUG-05 plus a 5 s `attendance/live` request cadence after logout.

### BUG-07 (S2) — `GET /attendance/logs` contract drift: `total` never returned
- **Where:** `app/static/app.js:519` reads `logs.total`; `app/api/attendance.py:137` returns only `{"items","limit","offset"}`
- **Actual:** the recognition-log header permanently renders "N of ?" (fallback prevents a crash).

### BUG-08 (S2) — Stored-XSS sink: unescaped `rtsp_url` in cameras table
- **Where:** `app/static/app.js:421` builds `src` from `c.rtsp_url` with a **no-op** `.replace(/^rtsp:\/\//, "rtsp://")`, interpolated unescaped at `:429` into `innerHTML`; the `file_path` branch *is* escaped. Backend `CameraCreate.rtsp_url` (`schemas.py:190`) has no format validation beyond length.
- **Impact:** a camera registered with an HTML/JS payload as `rtsp_url` executes script in every admin's browser; combined with the `localStorage` JWT (`app.js:54-58`) this is token theft. Requires an authenticated attacker who can create/edit a camera (or a compromised admin), so impact is privilege-preserving persistence rather than anonymous RCE.
- **Confidence:** sink CONFIRMED (code path unambiguous); exploitability limited by camera-management auth.
- **Verification after fix:** register a camera with `<img src=x onerror=alert(1)>` as rtsp_url → rendered inert/escaped.

### BUG-09 (S2) — `judge_demo`/`test_cctv` personal-path defaults
- **Where:** `scripts/run_demo.py:63` default `--video` = `C:\Users\pc\OneDrive\Pictures\WhatsApp Video 2026-10-02 at 16.13.08.mp4`; `scripts/extract_faces.py:28-30` and `scripts/enroll_from_clusters.py:101-102` hardcode the same OneDrive pattern; `scripts/check_accuracy.py:173` hardcodes `C:\Users\pc\AppData\Local\Temp\opencode\site.db`
- **Impact:** scripts fail on any other machine when defaults are used; leaks the original developer's username and file habits.

### BUG-10 (S2) — PII leak in tracked files (violates project's own privacy rule)
- **Where:** `test_drive.py:10` and `test_drive2.py:10` — comments containing a **real person's name and registration number**
- **Impact:** fails CONTRIBUTING's mandatory archive PII scan ("must print 0"); distributing this archive exposes the individual. Root scripts should not exist in the submission at all (see hygiene finding H-01).
- **Fix direction:** delete both files (they are scratch scripts referencing a missing CSV) — deletion, not redaction, since they serve no purpose.

### BUG-11 (S2) — Server-side `max_batch_frames` bounds requests, not frames
- **Where:** `app/inference/server.py:211-221` fill loop `len(raws) < settings.max_batch_frames` where `raws` are whole requests; `config.py:115` documents it as "max frames per engine call (across all cameras)"
- **Impact:** with camera `batch_size=8`, one engine call can carry 32 requests × 8 frames = 256 frames — 8× the documented cap — risking GPU memory pressure/OOM and contradicting the tuning knob's contract.
- **Verification after fix:** unit test feeding N oversized requests asserts engine receives ≤ `max_batch_frames` frames.

### BUG-12 (S2) — Celery black-hole route for `camera.run`
- **Where:** `app/workers/celery_app.py:41` routes `"camera.run" → "camera_slot_dynamic"`, a queue **no worker consumes** (`all_queues()` doesn't include it)
- **Actual today:** harmless because the API always overrides the queue via `send_task(..., queue=...)` (`cameras.py:240-241`). Any future `camera_run.apply_async()` without an explicit queue silently vanishes.

### BUG-13 (S2) — `INFERENCE_MODE=inprocess` is unwired in the worker
- **Where:** `config.py:111` documents `inprocess` = worker-local debug; grep shows nothing in `app/tasks.py`, `camera_task.py`, or worker startup installs `LocalInference().install()` — only `scripts/setup_student_database.py:434` and `scripts/test_cctv.py:284` do
- **Impact:** setting `INFERENCE_MODE=inprocess` on the worker (as the README/debug flow suggests) changes nothing; the pipeline always performs the Redis RPC (`camera_task.py:346`) and fails when no inference service exists.

### BUG-14 (S2) — Inference client exception-order bug
- **Where:** `app/inference/client.py:59-62` — `except redis_lib.RedisError` precedes `except TimeoutError`; in redis-py, `redis.exceptions.TimeoutError` subclasses `RedisError`
- **Actual:** socket timeouts surface as `InferenceServiceDown` instead of `InferenceTimeout`; the second handler is dead code for redis-py. Normally masked because the client's own BLPOP timeout fires first (`redis_client.py:37`), but any caller with a larger custom timeout gets a misleading error ~15 s in.

### BUG-15 (S2) — A single DB error kills a camera pipeline
- **Where:** `app/pipeline/camera_task.py:380-384` — `_handle_faces` (which does `db.commit()` at `:237`) runs without try/except in the main loop
- **Impact:** one failed commit (deadlock, serialization error, disk) terminates the whole camera task; the camera goes dark until manual restart or the 1-min reaper notices. Cleanup keeps Redis consistent, so this is availability, not corruption.

### BUG-16 (S2) — Hardcoded constants bypass configuration in the live pipeline
- **Where:** `app/pipeline/camera_task.py:188` (low-confidence alert threshold `0.55`), `:193` (alert dedup window `300` s vs `dedup_window_seconds=120`), `:292` (state TTL `86400`)
- **Impact:** operators cannot tune these via `.env`; they contradict the "one source of config" convention everywhere else (CONTRIBUTING rule 4).

### BUG-17 (S2) — Quality-gate config inconsistency for duplicate detection
- **Where:** `app/quality.py:197` uses `settings.recognition_threshold` (0.40) for cross-student duplicate-face rejection, while `app/matching.py:51,172` uses `settings.duplicate_identity_sim` (0.90) for the identical concept
- **Impact:** enrollment rejects a *different* person's face as "duplicate" at 0.40 cosine — far below the measured same-person floor (0.9096). Two config values govern one rule depending on code path.

### BUG-18 (S2) — Alerts dedup boundary mixes UTC timestamps with local dates
- **Where:** `app/services/alerts.py:116,143,174` compare `Alert.created_at` (UTC) against `datetime.combine(local_today, 00:00).replace(tzinfo=utc)`
- **Impact:** in non-UTC deployments (default README TZ is Asia/Kolkata), the "already alerted today" window is offset by the UTC offset — duplicate alerts near midnight, missed dedup after it. SUSPECTED-but-deterministic given any non-UTC `LOCAL_TIMEZONE`.

### BUG-19 (S3) — Redis-py `TimeoutError` shadowing documented; behavior divergence local vs server
- Covered by BUG-14; additionally `inference/local.py:76-77` pads unreadable frames with black images while `server.py:254-256` rejects the whole request — two policies to keep in sync.

### BUG-20 (S2) — `smoke_test.py` crashes on its first assertion
- **Where:** `scripts/smoke_test.py:65` calls `r.json()` on `GET /`, which returns HTML (`app/main.py:112-117`) → `json.JSONDecodeError`. Also defaults `ADMIN_PASSWORD=admin`.

### BUG-21 (S3) — `_CONFIRM_BUFFER` key leak
- **Where:** `app/pipeline/camera_task.py:46-70` — inner lists are pruned but the `"{camera}:{student}"` keys are never deleted; the dict grows for the process lifetime (bounded by students×cameras per prefork child, but unbounded across students over weeks).

### BUG-22 (S3) — `zip(jpegs, results)` silently truncates on short replies
- **Where:** `app/pipeline/camera_task.py:378` — if the inference server returns fewer results than frames, remaining frames are silently dropped with no log.

### BUG-23 (S3) — Latent `CameraOut` validation trap (possible 500 on `GET /cameras`)
- **Where:** `app/schemas.py:224` `CameraOut(CameraCreate)` inherits `_needs_source` (`:200-204`); `Camera` rows with both `rtsp_url` and `file_path` NULL are representable in the DB (`models.py:95-96`)
- **Actual:** the API's own create/update paths always leave ≥1 source, so this needs direct DB writes or a future bulk loader. SUSPECTED 500, not reachable through shipped endpoints.

### BUG-24 (S3) — WS auth checks signature only
- **Where:** `app/api/ws.py:29-34` calls `decode_token` only; user status/role are never verified against the DB
- **Impact:** a suspended or deleted user's still-valid JWT retains live-feed access (REST correctly re-checks status per request at `security.py:118-120`).

### BUG-25 (S3) — Legacy-token principal fallback can mint super admin
- **Where:** `app/security.py:113-116` — a validly-signed token whose subject matches `ADMIN_USERNAME` but has **no DB row** resolves to `super_admin` with `security_level=5`
- **Actual:** requires a token signed with the current `JWT_SECRET` (so it was legitimately issued at some point) for a user that no longer exists (e.g., deleted seeded admin). Account deletion therefore does not fully revoke access. LOW likelihood, real gap.

### BUG-26 (S3) — RTSP transport is process-global, first-writer-wins
- **Where:** `app/pipeline/frame_reader.py:82-85` `os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", ...)`
- **Impact:** in a multi-camera worker, the first camera's `RTSP_TRANSPORT` silently applies to every later camera.

### BUG-27 (S3) — Events lose a legitimate 0.0 ms latency; datetimes serialized inconsistently
- **Where:** `app/services/events.py:57,76` `if inference_ms else None` (0.0 → None); `:27-28` raw datetimes rely on `json.dumps(default=str)` while sibling fields use `.isoformat()`; naive SQLite datetimes serialize without offset.

### BUG-28 (S2) — `timeutil.local_tz()` silently falls back to UTC on bad TZ
- **Where:** `app/timeutil.py:15-19` swallows all exceptions; a typo'd `LOCAL_TIMEZONE` shifts every attendance `date` to UTC with only a debug-level symptom (no warning surfaced).

### BUG-29 (S3) — `enrollment.replace_face` skips the duplicate-identity check
- **Where:** `app/services/enrollment.py:535-548` — no `check_duplicate_face` call, unlike `enroll_student`; a second person's face can be attached to an existing student, defeating the 409 protection.

### BUG-30 (S4) — Debug prints in production paths
- **Where:** `app/api/students.py:68`; `app/services/enrollment.py:244,380,383,385,396,471`
- **Impact:** bypass the JSON logging config (`logging_config.py`), pollute stdout in Docker, and leak enrollment internals (row counts, reasons) into container logs.

## B. Suspected problems (not yet confirmed)

| ID | Finding | Evidence | Why not confirmed |
|---|---|---|---|
| SUS-01 | `bulk-import` CSV photo paths may allow reading files outside `DATA_DIR` via `..` segments (`students.py:268-274` passes CSV-supplied relative paths to `enrollment.bulk_enroll_from_csv`, which joins under `data_dir`; no explicit normalization/guard seen) | `app/api/students.py`, `app/services/enrollment.py:219-472` | Path-join semantics need runtime verification; flagged for the security phase (06-S10) |
| SUS-02 | `attendance._session_out` dead branch (`:219-221`) may have been intended to load period names for legacy rows | code read | cosmetic unless legacy rows exist |
| SUS-03 | `_gpu_status_age`/health treat a *fresh* but empty `gpu:status` hash as "unknown" — compose healthcheck uses `/system/health`, which is "degraded" until the inference service publishes its first heartbeat (~2 s after boot); with `start_period: 40s` this is fine, but a slow GPU load could flap the healthcheck | `system.py:37-50`, compose `:78-84` | needs a running stack |
| SUS-04 | `redis_client.get_camera_runtime` O(slots) scan per camera → `GET /cameras` is O(cameras×slots) Redis GETs; with 8 slots it is minor, with many cameras it degrades | `redis_client.py:81-125`, `cameras.py:133-139` | scale-dependent, unmeasured |
| SUS-05 | `GET /students` decrypts every matched student's full embedding blob to compute `embedding_count` (`students.py:34` → `models.py:65-77`), i.e., PBKDF2+Fernet work per row per request | code path unambiguous | performance class, measured impact unknown (see 07) |

## C. Unverified behavior (cannot be assessed statically)

| Area | Why unverified |
|---|---|
| The entire 148-function test suite | No Python interpreter exists in this environment |
| Import-time behavior (e.g., whether `app.main` truly boots without cv2/ORT installed) | Same |
| RTSP reading, reconnect/backoff, slot scheduling against a live camera | No camera; README admits never validated |
| GPU inference quality/performance, cross-camera batching under load | No GPU, no model weights in archive |
| PostgreSQL runtime (migrations, JSON-vs-String column, JSONB behavior) | No database server; SQLite-only paths unexercised |
| Celery worker topology end-to-end | Missing `run_worker.sh`; no broker |
| `alembic upgrade head` on a fresh DB | No Python/database |
| Dependency vulnerability status (`pip audit` etc.) | No Python/pip; versions pinned but CVE status not checked |

## D. Repository hygiene findings (not runtime bugs)

- **H-01:** 12 root-level ad-hoc scripts committed to the submission: `add_fallback.py`, `add_require_all.py` (appears syntactically invalid — an `if` statement without a body was reported in `:30`), `add_require_enforce.py`, `add_require_logic.py`, `apply_fix.py`, `fix_require_all.py` (targets a `parse_args(argv)` signature that no longer exists), `analyze_csv.py`, `list_students.py`, `test_drive.py`, `test_drive2.py` (PII — BUG-10), `create_zip.py` (references nonexistent `scripts/test_judge_demo.py`, `scan.py`, `pii_ext.py`, `build_zip.py`), `build_handoff.py` (unused `EXCLUDE_FILES`).
- **H-02:** `tests/test_pipeline_e2e.py` "simulates" the worker by writing its own `RecognitionLog` rows rather than exercising `camera_task` — an end-to-end test that doesn't test the end.
- **H-03:** `tests/test_registration.py` asserts a locally-defined regex `^\d{8,12}$` and never imports app code — a test that tests nothing about the app.
- **H-04:** README test count ("69 tests", `README.md:203`) vs actual 148 test functions (verified by regex count) — documentation is from an older revision.
- **H-05:** `pyproject.toml:17` (`celery[redis]`) contradicts `requirements.txt:43-49` (deliberately not `celery[redis]`).
