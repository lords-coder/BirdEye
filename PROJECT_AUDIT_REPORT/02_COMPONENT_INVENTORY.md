# 02 — Component Inventory

Status legend: **COMPLETE** (implemented, internally verified by code reading) · **PARTIAL** · **PLACEHOLDER** · **DISCONNECTED** (exists but not wired into the running system) · **BROKEN** (reproducible defect) · **UNVERIFIED** (cannot be confirmed without runtime).

> No component could be executed in this environment (no Python interpreter). "COMPLETE" here means *complete as read* — statically coherent, schema-consistent, and covered by tests that exist (though the tests themselves were not run).

## 1. Frontend (`app/static/` — vanilla SPA, served at `/`)

Single-page app; views toggled via the `hidden` attribute. 36 API/WS call sites, all verified against backend routes.

| # | View / component | File:lines | Purpose | Depends on | Status | Issues |
|---|---|---|---|---|---|---|
| 1 | Login card | `index.html:11-31`, `app.js:123-138` | username/password → `POST /auth/token`, stores JWT in `localStorage["iva_token"]` | `/auth/token` | COMPLETE | — |
| 2 | Register card | `index.html:33-60`, `app.js:140-165` | self sign-up → pending account | `/auth/register` | COMPLETE | — |
| 3 | Forgot-password card | `index.html:62-79`, `app.js:167-185` | reset request to super admin | `/auth/forgot-password` | COMPLETE | — |
| 4 | App shell (sidebar/topbar/toasts) | `index.html:81-109` | 7-section nav; Users section hidden unless `role==super_admin` | `/auth/me` | COMPLETE | Users console unreachable when `AUTH_REQUIRED=false` (no token ⇒ nav hidden) even though backend treats all callers as super admin (`security.py:95-98`) |
| 5 | Dashboard | `index.html:111-124`, `app.js:261-306` | 5 stat cards + GPU detail | `/system/health`, `/system/gpu-status`, `/students?limit=1`, `/cameras`, `/attendance/summary` | COMPLETE | health "unknown"/"stale" values counted as down (cosmetic); camera sub-count inconsistent with table logic |
| 6 | Students section | `index.html:126-159`, `app.js:309-401` | list (limit 200, no pagination), enroll 3-5 photos, add photos, clear face, delete | `/students*` | PARTIAL | "add photos" is **guaranteed to fail** (backend Bug #1); no student edit; no bulk-import; no pagination UI; stuck "loading…" on error |
| 7 | Cameras section | `index.html:161-198`, `app.js:405-491` | register (RTSP/file), start/stop pipeline, delete | `/cameras*` | PARTIAL | **stored-XSS sink**: unescaped `rtsp_url` via `innerHTML` (`app.js:421,429`); no camera edit; `#c-file` maxlength 550 vs schema 500 |
| 8 | Attendance section | `index.html:200-230`, `app.js:493-533` | daily summary + recognition logs by date | `/attendance/summary`, `/attendance/logs` | PARTIAL | header reads `total` which the API never returns → always "N of ?"; no period views; no session close; limit fixed at 200 |
| 9 | Live section | `index.html:232-251`, `app.js:535-581` | ongoing sessions (5 s poll) + WS event feed | `/attendance/live`, `WS /ws/live-feed` | PARTIAL | **infinite WS reconnect after logout** (`app.js:578` guard always true); `logout()` leaks `liveTimer` and `wsRetry`; no client ping despite server pong support |
| 10 | Users console (super admin) | `index.html:253-307`, `app.js:585-765` | approve/reject/suspend, credentials, password-reset queue | `/auth/admin/*` | COMPLETE | approve uses `window.prompt()`; heavy list re-fetch to prefill forms |
| 11 | System section | `index.html:309-319`, `app.js:753-765` | raw JSON dumps of health + gpu-status | `/system/*` | COMPLETE | — |
| — | Periods UI | — | — | `/periods` (5 routes) | **MISSING** | zero frontend coverage |
| — | Alerts UI | — | — | `/alerts` (4 routes) | **MISSING** | zero frontend coverage |

Cross-cutting frontend facts (verified): no external CDN/network resources, no inline styles/scripts, no hardcoded hosts or secrets; token sent as `Authorization: Bearer` on every call; 401 auto-logout. Accessibility gaps: no `aria-live` on toasts/events, no labels on several controls, focus not managed on view switches, emoji read by screen readers, `.btn.primary` contrast ≈3.8:1 (< WCAG AA).

## 2. Backend — API layer (`app/api/`)

| Module | Routes | Purpose | Status | Issues |
|---|---|---|---|---|
| `auth.py` (388 L) | 11 | token, register (pending), forgot-password (non-enumerating), me, admin user CRUD/approve/reject/suspend, password-request queue | COMPLETE | last-super-admin protection implemented; no rate limiting (see 06) |
| `students.py` (309 L) | 9 | enroll, list (paginated), get, update, add photos, replace face, clear face, bulk-import, delete | BROKEN (partial) | `POST /{id}/photos` dead (Bug #1); leftover `print("DEBUG...")` at `:68`; `GET ""` returns bare `dict` (no response model) |
| `cameras.py` (340 L) | 7 | registry CRUD + slot-based start/stop with Redis NX slot claim, heartbeat, force-revoke | COMPLETE (static) | `_celery_or_503()` helper defined but never used (dead code, `:83-84`); start/stop unverifiable without Redis/Celery — UNVERIFIED at runtime |
| `attendance.py` (189 L) | 7 | summary, live, logs, per-period summary, per-period-per-student, per-student, force-close | COMPLETE | close_session uses private service helpers with `# type: ignore` (`:39-47`); `GET ""`-style dict response for logs |
| `periods.py` (98 L) | 5 | timetable CRUD | COMPLETE | no frontend consumer |
| `alerts.py` (85 L) | 4 | list/get/ack/resolve | COMPLETE | no frontend consumer; no authz granularity beyond "any admin" |
| `system.py` (149 L) | 2 | health (public), gpu-status (auth) | COMPLETE | `/health` name in README doesn't exist (actual: `/system/health`) |
| `ws.py` (85 L) | 1 WS | `/ws/live-feed`: backlog replay (100) + Redis pub/sub; `?token=` auth | PARTIAL | auth checks **signature only** — suspended/deleted users keep live-feed access; JWT in query string (log leakage); loose `"ping" in data[:40]` match |

## 3. Backend — services & core (`app/`)

| Module | Purpose | Used by runtime? | Status | Key issues |
|---|---|---|---|---|
| `services/enrollment.py` (567 L) | enrollment, per-photo gates, embedding, duplicate-face rejection, CSV bulk import, photo persistence | Yes (API) | BROKEN (partial) | **Bug #1** `add_photos:495`; 6 debug prints; `replace_face` skips duplicate-face check; per-row commit + Redis INCR in bulk import; inconsistent ID masking (first-3 vs last-3 elsewhere) |
| `services/attendance.py` (530 L) | session state machine, summaries, live, per-period | Yes | COMPLETE | `summary_for_date` ↔ `period_summary` ~90 % duplicated; dead `elif` in `_session_out:219-221`; timezone string derived from `tzinfo` repr (`:355,477`) |
| `services/alerts.py` (189 L) | alert creation + period deviation evaluation | Yes (beat task + camera task) | PARTIAL | hard-coded 15/5/10-min grace windows; "today" dedup boundary computed in UTC against a local date (`:116,143,174`); per-alert commits; unused imports (`rc`, `settings`) |
| `services/events.py` (100 L) | WS payload builders + publish | Yes | COMPLETE | `if inference_ms else None` loses a legitimate 0.0 ms; raw datetimes serialized via `default=str` |
| `matching.py` (197 L) | EmbeddingIndex: decrypt-all → numpy matrix → threshold/margin/dup-identity | Yes (camera task) | COMPLETE | dead `scan_top` and `cosine_similarity_batch`; O(students×rows) runner-up grouping recomputed per probe; full-gallery decrypt on every refresh |
| `quality.py` (200 L) | enrollment photo gate + cross-student duplicate check | Yes (enrollment) | PARTIAL | **config inconsistency**: duplicate check uses `recognition_threshold` (0.40) at `:197` while `matching.py` uses `duplicate_identity_sim` (0.90) for the same concept; swallows `InferenceError` → GPU outage reported as "no face detected"; sharpness/brightness settings not enforced here (only in engine) |
| `crypto.py` (108 L) | Fernet encrypt/decrypt of embeddings; PBKDF2 key derivation (fixed salt, 100k iters) | Yes | COMPLETE | `_fernet` computed at import (100k PBKDF2 on every process start); fixed salt ⇒ deterministic ciphertexts (documented) |
| `users.py` (238 L) | PBKDF2-SHA256 hashing, account lifecycle, seed super-admin, .env password fallback w/ re-sync | Yes | COMPLETE | best-engineered module; no lockout/rate-limit (policy gap, not a bug) |
| `security.py` (149 L) | JWT issue/decode, Principal resolution, guards | Yes | PARTIAL | legacy-token fallback (`:113-116`) grants `super_admin` to any token whose subject equals `ADMIN_USERNAME` even when no DB user exists; no revocation |
| `redis_client.py` (183 L) | pools, camera runtime/heartbeat/slot keys, dedup gate, pub/sub, students-version | Yes | COMPLETE | `get_camera_runtime` does O(slots) GETs per camera; dedup/version-bump silently disabled on Redis outage (documented) |
| `tasks.py` (127 L) | Celery tasks: `camera.run`, stale-session closer, deviation checker, dead-camera reaper | Via Celery `include` only | PARTIAL | reaper swallows all errors (`:76-81,105-106,123-126`); stale closer duplicates `scripts/close_stale_sessions.py`; **no test imports it** |
| `db.py` (53 L) | engine/session factory | Yes | COMPLETE | `autoflush=False` forces manual `flush()` workarounds in attendance service |
| `timeutil.py` (34 L) | UTC/local date helpers | Yes | COMPLETE | `parse_date` dead; `ZoneInfo` rebuilt per call; invalid TZ silently falls back |
| `logging_config.py` (128 L) | JSON/text logging | Yes | COMPLETE | `get_logger`/`Timer` dead; root-logger takeover |
| `config.py` (239 L) | all settings + threshold derivations | Yes | COMPLETE | `LOCAL_TIMEZONE` default `UTC` vs README's `Asia/Kolkata` |
| `confirm.py` (228 L) | offline K-of-N confirmation, box tracker, ledger | **No — scripts/tests only** | DISCONNECTED | parallel implementation of camera_task's `_CONFIRM_BUFFER` with different constants (3/10 s vs config 2/5 s) |
| `formcsv.py` (425 L) | Google-Forms CSV parsing, Drive URL classification, frame selection | **No — scripts/tests only** | DISCONNECTED | hard-coded quality constants diverge from `config.py` (sharpness 40 vs 30, brightness max 225 vs 230) |
| `evalmetrics.py` (178 L) | FAR/FRR/precision/recall metrics | **No — scripts/tests only** | DISCONNECTED | clean; zero-denominator returns 0.0 silently |

## 4. AI/ML modules (`app/inference/`, `app/pipeline/`)

| Module | Purpose | Status | Key issues |
|---|---|---|---|
| `inference/engine.py` (612 L) | `FaceEngine`: batched SCRFD decode (custom NMS, letterbox), align, quality gate, batched ArcFace embedding, CUDA→CPU fallback, startup canary | COMPLETE (static) / UNVERIFIED (runtime — no GPU/model weights here) | deep coupling to insightface **private** APIs (`_resolve_input_sizes`, `_session_for_input_size`, `_feat_stride_fpn`…); silent 512-d truncation of non-512 models (`:515-516`); quality gate enforces only area/brightness/sharpness — the config's pose/width/det-score gates are **not** applied on the live path; faces trimmed *after* embedding (wasted GPU) |
| `inference/server.py` (382 L) | shared GPU service: BLPOP → coalesce ≤25 ms → batch infer → per-request reply; `gpu:status` every 2 s | COMPLETE (static) / UNVERIFIED | **`max_batch_frames` bounds requests, not frames** (`:211-221`) → up to 32×8=256 frames per engine call vs documented 32; one bad JPEG rejects the whole request; cosmetic `pid=0` (`:171`) |
| `inference/client.py` (91 L) | Redis RPC: `infer_frames`, `embed_images` | COMPLETE | exception-order bug (`:59-62`): redis-py `TimeoutError` is caught by the earlier `RedisError` handler → misleading `InferenceServiceDown`; custom timeouts > setting get dropped server-side |
| `inference/local.py` (125 L) | in-process engine for scripts; monkeypatches `client` | COMPLETE for its purpose | imports cv2 at module scope (scripts only, acceptable); behavior divergence: unreadable frames padded black vs server rejecting |
| `pipeline/frame_reader.py` (245 L) | decode thread: sampling, bounded drop-oldest queue, exponential-backoff reconnect, file-loop as mock camera | COMPLETE (static) / UNVERIFIED (no RTSP exercised) | `OPENCV_FFMPEG_CAPTURE_OPTIONS` set process-globally first-writer-wins (`:82-85`) → per-camera transport is a lie in multi-camera workers; dead `_file_start_pts_ms`; magic `5`-frame EOF heuristic |
| `pipeline/camera_task.py` (450 L) | per-camera loop: batch → JPEG → RPC → match → K-of-N → dedup → RecognitionLog + attendance + WS events | COMPLETE (static) / UNVERIFIED | hardcoded 0.55 low-confidence alert (`:188`), 300 s alert dedup (`:193`), 86400 TTL (`:292`) bypass config; `_CONFIRM_BUFFER` keys never deleted (leak); a single DB error kills the pipeline (no try/except around `_handle_faces`, `:380-384`); `zip()` silently truncates on short server replies |
| `workers/celery_app.py` (77 L) | queues: `control`, `jobs`, `camera_slot_0..N`; beat schedule | PARTIAL | route `"camera.run"→camera_slot_dynamic` (`:41`) points at a queue no worker consumes (black-hole trap); `all_queues()` dead; worker start script missing (see 05) |
| — `INFERENCE_MODE=inprocess` | documented debug mode | **DISCONNECTED** | nothing in the worker/camera path ever calls `LocalInference().install()`; only scripts do. Setting the flag on the worker has no effect |

## 5. Database

8 tables via 4 Alembic migrations (chain intact: 0001→0004). Full column/relationship detail in `03_API_AND_DATABASE.md`. Known mismatch: `students.embeddings` JSON (migration) vs String (model).

## 6. Scripts (`scripts/`, 18 files) — summary

| Group | Files | Status |
|---|---|---|
| Documented & clean | `wait_db.py`, `generate_key.py`, `download_models.py`, `bulk_enroll.py`, `seed.py`, `make_test_video.py`, `benchmark.py`, `close_stale_sessions.py` | COMPLETE |
| Documented but flawed | `run_demo.py` (hardcoded personal WhatsApp-video default path `:63`), `rotate_encryption_key.py` (`os.chdir` side effect) | PARTIAL |
| Undocumented + flawed | `judge_demo.py` (**TypeError Bug #3**, 42 KB), `smoke_test.py` (crashes: calls `.json()` on HTML `GET /` response `:65`), `check_accuracy.py` (hardcoded personal temp path + admin/admin creds; fallback path reports "perfect detection"), `test_cctv.py` (test-named file outside `tests/`), `setup_student_database.py`, `extract_faces.py`, `enroll_from_clusters.py`, `cluster_faces.py` (last two require missing `data/dataset_index/*`; two hardcode OneDrive WhatsApp paths) | BROKEN/PARTIAL |
| Root debris (12) | `add_*.py` ×4, `apply_fix.py`, `fix_require_all.py` (in-place patchers; `add_require_all.py` appears syntactically invalid), `analyze_csv.py`, `list_students.py`, `test_drive.py`, `test_drive2.py` (**contain real PII**), `create_zip.py` (references missing files), `build_handoff.py` | REMOVE |

## 7. External integrations

| Integration | Mechanism | Status |
|---|---|---|
| RTSP CCTV cameras | `cv2.VideoCapture` via FFmpeg (`RTSP_TRANSPORT`, timeouts, backoff) | code complete; **never validated against a physical camera** (README admits) |
| NVIDIA GPU | onnxruntime-gpu + pynvml telemetry + compose `resources.reservations` | UNVERIFIED (no GPU here); GPU Compose stack never run (README admits) |
| InsightFace model zoo | `buffalo_l` weights downloaded by `scripts/download_models.py`, cached `~/.insightface`, compose volume `insightface_models` | weights absent from archive (correctly — privacy rule) |
| Email/SMS for password reset | — | **absent by design**: reset requests route to super admin in-app |
| Google Forms/Drive (enrollment) | `app/formcsv.py` classifies Drive URLs for direct download | DISCONNECTED — no HTTP fetch anywhere; helpers only used by scripts/tests |
