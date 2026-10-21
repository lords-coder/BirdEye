# 09 — Recommended Fix Roadmap

**This roadmap is a plan only. No fix has been implemented.** Complexity: S (hours) / M (1-2 days) / L (multi-day). Each task lists acceptance criteria (AC) and verification steps (V).

## P0 — Critical security issues & submission blockers

### P0-1 · Remove PII from tracked files · S · deps: none
- **Modules:** `test_drive.py`, `test_drive2.py` (root)
- **Action:** delete both files (scratch scripts for a nonexistent CSV; also remove `analyze_csv.py`, `list_students.py` — same class, zero functionality).
- **AC:** repo-wide case-insensitive scan for the leaked identity returns 0 matches.
- **V:** `Select-String -Pattern '<name>|<regno>' -Recurse` → 0; project PII scan gate passes.

### P0-2 · Fix stored XSS in cameras table · S · deps: none
- **Modules:** `app/static/app.js:421,429`; `app/schemas.py:190`
- **Action:** escape `src` like the `file_path` branch; add server-side `rtsp_url` scheme validation (`rtsp://`/`rtsps://`).
- **AC:** camera with `<img src=x onerror=...>` as `rtsp_url` renders inert; API rejects non-rtsp schemes with 422.
- **V:** manual UI test + unit test on `CameraCreate`.

### P0-3 · Gate `/media` behind authentication · M · deps: none
- **Modules:** `app/main.py:136-139`, new photo-streaming route
- **Action:** replace the anonymous `StaticFiles` mount with an authenticated route (or dependency-wrapped static handler); ensure `/media` 401s without a token.
- **AC:** unauthenticated `GET /media/students/<reg>/photo_0.jpg` → 401; with token → 200.
- **V:** tests in `tests/test_api.py` style using `client` + `auth_header` fixtures.

### P0-4 · Authentication hardening · M · deps: none
- **Modules:** `app/main.py` (middleware), `app/api/auth.py`, `app/api/ws.py`, `app/security.py`
- **Action:** per-IP/per-user rate limit on `/auth/token` and `/auth/forgot-password`; make WS resolve the full principal (status check); fail closed in `resolve_principal` when no DB user matches (removes super-admin revival).
- **AC:** 10 rapid bad logins → 429; suspended user's valid JWT rejected on WS connect; deleted-user token → 401.
- **V:** unit tests with fakeredis-backed counters; WS test via TestClient.

### P0-5 · Repository hygiene sweep · S · deps: P0-1
- **Modules:** 12 root debris scripts; `print("DEBUG...")` at `app/api/students.py:68` + 6 sites in `app/services/enrollment.py`
- **Action:** delete debris (`add_*.py` ×4, `apply_fix.py`, `fix_require_all.py`, `create_zip.py`, `build_handoff.py`); replace debug prints with structured logs or remove.
- **AC:** root contains only project manifests + docs; no `print(` in `app/`.
- **V:** file listing review; grep.

## P1 — Core functionality & integration failures

### P1-1 · Fix `enrollment.add_photos` (BUG-01) · S · deps: none
- **Modules:** `app/services/enrollment.py:491-512`
- **Action:** count decrypted embeddings (`student.embedding_list`), not the ciphertext string; fix `start_index`.
- **AC:** enroll 3 photos → add 1 more via API → 200, `embedding_count=4`; adding beyond `MAX_REFERENCE_PHOTOS` → 4xx.
- **V:** regression test through `POST /students/{id}/photos` (fails before fix).

### P1-2 · Create Dockerfile + `scripts/run_worker.sh` (BUG-02) · M · deps: P1-5
- **Modules:** new `Dockerfile`, new `scripts/run_worker.sh`; `docker-compose.yml`
- **Action:** CUDA-base image per the documented recipe (ORT GPU swap, headless OpenCV); worker script launching `celery -A app.workers.celery_app worker` with all slot queues + `--beat` (or a separate beat service).
- **AC:** `docker compose up -d` → all 5 services healthy; `/system/health` reports ok; worker consumes `camera_slot_0`.
- **V:** compose config lint + full stack smoke (needs GPU host for inference; CPU fallback acceptable for CI).

### P1-3 · Reconcile `students.embeddings` column type (BUG-04) · M · deps: none
- **Modules:** new `alembic/versions/0005_*.py`
- **Action:** migration altering `students.embeddings` JSON → TEXT with `server_default=""`; data migration for existing `'[]'` rows if any.
- **AC:** fresh `alembic upgrade head` + enroll + read works on PostgreSQL **and** SQLite; `alembic check` reports no model drift.
- **V:** integration test running migrations against scratch SQLite; manual Postgres pass.

### P1-4 · Fix `judge_demo.py` main() call (BUG-03) · S · deps: none
- **Modules:** `scripts/judge_demo.py:244`, `scripts/setup_student_database.py:349`
- **Action:** call `setup_student_database.main()` without argv (or add an `argv` parameter to `main`).
- **AC:** STEP 2 executes without TypeError (mock the DB/model parts).
- **V:** un-stub one `test_judge_demo` path or add a signature test.

### P1-5 · Restore onboarding artifacts · M · deps: none
- **Modules:** new `.env.example`, `requirements-dev.txt`, `scripts/setup_dev.ps1/.sh`; README §3/§11 rewrite or Makefile
- **Action:** generate `.env.example` from `config.py` (no secret values); dev requirements = pytest + httpx + fakeredis + linter; setup script per README's 7 steps; either add the Makefile or delete the `make` paragraph.
- **AC:** a clean machine can follow README §3 verbatim and reach a running API + green suite.
- **V:** execute the documented flow on a clean VM/container.

### P1-6 · Fix frontend logout/reconnect defects (BUG-05/06) · S · deps: none
- **Modules:** `app/static/app.js:57,60-67,578`
- **Action:** `getToken()` returns `null` when unset **or** guard on truthiness; `logout()` clears `liveTimer` + `wsRetry`; bind WS handlers to the event's own socket.
- **AC:** after logout: zero further HTTP/WS attempts except a single deliberate probe; no timer activity.
- **V:** browser devtools network timeline; add a small JS test harness if introduced.

### P1-7 · Fix server batch-cap semantics (BUG-11) · M · deps: none
- **Modules:** `app/inference/server.py:211-257`
- **Action:** cap accumulated **frames** (sum of `len(request["frames"])`) at `MAX_BATCH_FRAMES`; document behavior.
- **AC:** unit test with oversized requests proves engine receives ≤ cap frames.
- **V:** test injecting fake engine + fake redis.

## P2 — Incomplete features & reliability

### P2-1 · Config consistency pass · M · deps: none
- Replace hardcoded pipeline constants (`camera_task.py:188,193,292`) with settings; unify duplicate-sim threshold (`quality.py:197` → `duplicate_identity_sim`); make alert grace windows configurable (`services/alerts.py:94,95,168`); fix alerts UTC/local day boundary (`:116,143,174`); align `confirm.py` docstring constants with `config.py`.
- **AC:** one source of truth per threshold (CONTRIBUTING rule 4); tests updated.
- **V:** unit tests asserting settings flow; TZ-parametrized alert dedup test.

### P2-2 · Camera pipeline resilience · M · deps: none
- try/except per `_handle_faces` iteration (log + continue, BUG-15); delete `_CONFIRM_BUFFER` keys on clear (BUG-21); log + count short server replies (BUG-22); wire `INFERENCE_MODE=inprocess` in worker startup or remove it from docs (BUG-13); fix client exception order (BUG-14); per-camera RTSP transport (BUG-26).
- **AC:** injected DB failure does not kill the pipeline; a camera survives N transient errors.
- **V:** new `tests/test_camera_task.py` (currently missing) with fakes.

### P2-3 · Celery routing cleanup · S · deps: P1-2
- Remove/repair the `camera_slot_dynamic` route (`celery_app.py:41`); use `all_queues()` in the worker script or delete it; align `pyproject.toml` `celery[redis]` with `requirements.txt`.
- **AC:** no route references an unconsumed queue; single dependency source of truth.
- **V:** worker boot log shows expected queues.

### P2-4 · Frontend contract fixes · S · deps: none
- Add `total` to `GET /attendance/logs` (or stop reading it, `app.js:519`); fix `#c-file` maxlength 550→500; replace stuck `loading…` rows with error rows; remove unused `{}` JSON bodies.
- **AC:** log header shows "N of M"; 501+ char paths blocked client-side.
- **V:** UI walkthrough.

### P2-5 · WS lifecycle hardening · M · deps: P0-4, P1-6
- One-time WS tickets or header-based auth; client ping keepalive; exponential backoff with cap.
- **AC:** token never appears in URLs; reconnect storm impossible.
- **V:** proxy-log inspection; network panel.

### P2-6 · Test-suite reality check · M · deps: P1-5
- Delete/repair vacuous tests (`test_registration.py` — wire to real `registration_no_pattern`; `test_pipeline_e2e.py` — actually drive `camera_task` with fakes); add tests for `app/tasks.py`, `inference/client.py` timeouts, `inference/server.py` batching; update README count dynamically.
- **AC:** suite covers every module that has runtime consumers; README test count accurate.
- **V:** `pytest --cov=app` (introduce coverage as dev dep); mutation-check the two fixed tests.

### P2-7 · Upload limits · S · deps: none
- Cap per-photo and CSV upload sizes; stream to temp with limits (`students.py`).
- **AC:** 200 MB upload → 413; API memory stable.
- **V:** test with oversized payload.

## P3 — Performance, maintainability, UI/UX, optional

### P3-1 · De-duplicate attendance service · M
Merge `summary_for_date`/`period_summary` and `student_attendance`/`period_student_attendance` into parameterized cores (~90 % duplicated, `attendance.py:295-530`); same for `tasks.py` vs `scripts/close_stale_sessions.py`; unify quality-gate logic (`quality.py` vs `engine.py:465-487`); unify ID masking (first-3 vs last-3).
- **V:** existing 148-test suite stays green.

### P3-2 · Performance pass · M · measurable claims require a running stack
- Stop decrypting embeddings for `embedding_count` in list endpoints (cache counts or store a count column; SUS-05); batch alerts commits (`services/alerts.py:41`); O(1) slot lookup (`redis_client.py:117-125`); review bulk-import per-row commit + Redis INCR (`enrollment.py:468-469`); consider ANN index if gallery > ~1k (linear scan documented).
- **AC:** p95 `GET /students` with 500 students < 200 ms locally (measure before/after).

### P3-3 · Missing UI areas · L
Periods management, alerts center, student edit/replace-face, bulk-import wizard, camera edit, pagination controls, per-period attendance views, session force-close button.
- **AC:** every route in 03 §1 has a UI consumer or an explicit "API-only" decision recorded.

### P3-4 · Dead code & consistency cleanup · S
Remove: `matching.scan_top`, `matching.cosine_similarity_batch`, `timeutil.parse_date`, `logging_config.get_logger/Timer`, `cameras._celery_or_503`, `attendance._session_out` dead elif, `enrollment` unreachable checks, redundant double indexes (optional migration), `_file_start_pts_ms`. Fix `CameraOut` inheritance trap (`schemas.py:224` — stop inheriting `_needs_source`).
- **V:** grep for each symbol = 0 usages; suite green.

### P3-5 · Documentation refresh · M · deps: P1-* 
Rewrite README §1 statuses honestly post-fix; correct `/health`, `/ws/live-feed`, TZ default, test counts; restore or delete `eval/` references; document the two-role RBAC and the non-enforced `security_level` (or implement enforcement — separate task).
- **AC:** every file/path/endpoint named in README exists; a doc-drift CI check (script asserting referenced paths exist) passes.

### P3-6 · Optional hardening
`security_level` route enforcement (currently stored, never used); audit log for admin actions; structured request logging middleware; accessibility pass (aria-live, labels, focus management, contrast per 02 §1); `pip-audit` in CI; dependency CVE review (SEC-16).

## Dependency graph (summary)

```
P0-1 ─► P0-5
P1-2 ─► P2-3
P0-4 ─► P2-5
P1-5 ─► P2-6 ─► (suite as safety net for all later work)
P1-* mostly independent; P3-1..4 should follow P2-6 so the suite protects refactors
```

**Suggested sequencing:** P0 (all) → P1-1/P1-3/P1-4 (quick functional wins) → P1-2/P1-5 (restore build & onboarding) → P2-6 (make tests trustworthy) → remaining P2 → P3.
