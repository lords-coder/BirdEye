# 02 — Bugs Fixed

IDs match `PROJECT_AUDIT_REPORT/05_BUGS_AND_DEFECTS.md` and
`06_SECURITY_AUDIT.md`. "Proof" names the automated regression test in
`tests/test_repair_regressions.py` (prefix `RR:`), another test file, or the
live smoke check that demonstrates the fixed behaviour. Every item below was
run and observed — nothing is claimed on inspection alone.

Severity: S1 blocker · S2 major · S3 minor · S4 cosmetic (audit scale).

## A. Audited defects fixed

| ID | Sev | Defect | Fix | Proof |
|---|---|---|---|---|
| BUG-01 | S1 | `POST /students/{id}/photos` could never succeed: `add_photos` used the **ciphertext string length** as the next photo index and compared `int` against `str` | `app/services/enrollment.py` rewritten: decrypt embeddings first, numeric index from decrypted count, correct bounds check | RR `test_add_photos_appends_embeddings_and_continues_file_index` |
| BUG-02 | S1 | Deployment impossible: no `Dockerfile`, no worker start script | Created `Dockerfile` (non-root, healthcheck, ORT-GPU swap) and `scripts/run_worker.sh`; `docker-compose.yml` `DATA_DIR`/`INSIGHTFACE_ROOT` mounts wired to real container paths | structure verified; compose stack still GPU-unverified (06) |
| BUG-03 | S1 | `judge_demo.py` → `setup_student_database.main(argv)` `TypeError` | `main(argv=None)` + `parse_args(argv)` added | RR `test_setup_student_database_main_accepts_argv` |
| BUG-04 | S1 | `students.embeddings` JSON in migration vs String/TEXT in model | New migration `0005_embeddings_text` (JSON→TEXT, `server_default=''`, PG `USING` casts) | RR `test_alembic_upgrade_head_and_check_no_drift`; verified upgrade→downgrade→upgrade |
| BUG-05 | S2 | Infinite WS reconnect after logout | `app.js`: reconnect guard `if (getToken())` before retry | RR static guard `test_web_console_static_guards_stay_in_place` |
| BUG-06 | S2 | `logout()` leaks `wsRetry` / `liveTimer` | `app.js`: both cleared in `logout()` | same static guard |
| BUG-07 | S2 | `GET /attendance/logs` contract drift: `total` never returned | `app/api/attendance.py`: `func.count` total in response | RR `test_attendance_logs_returns_total` |
| BUG-08 | S2 | Stored-XSS sink: unescaped `rtsp_url` in cameras table | `app.js`: `esc(c.rtsp_url)`; backend: `rtsp_url` scheme validators (see SEC-02) | static guard + RR camera schema tests + live 422 |
| BUG-09 | S2 | `judge_demo`/`test_cctv`/4 scripts personal-path defaults | All personal paths removed: `run_demo.py` → `samples/videos/lecture.mp4`; `check_accuracy.py` → `SITE_DB` env + repo `data/site.db`; `extract_faces.py` → explicit `--videos` required; `enroll_from_clusters.py` → `samples/` filename lookup | repo-wide scan: **0 matches** |
| BUG-10 | S2 | PII (real name + registration number) in tracked `test_drive*.py` | Files deleted (root debris sweep, H-01) | PII scan: **0 matches** |
| BUG-11 | S2 | `max_batch_frames` bounded requests, not frames (32×8=256 frames) | `app/inference/server.py`: `_frame_count()` helper; batch coalescing capped by **frames** | RR `test_inference_server_batch_capped_by_frames` |
| BUG-12 | S2 | Celery route `camera.run`→`camera_slot_dynamic` black hole | Dead route removed from `app/workers/celery_app.py` | RR `test_celery_queues_and_no_dead_route` |
| BUG-13 | S2 | `INFERENCE_MODE=inprocess` unwired in worker | `app/tasks.py`: in-process engine installed in `camera_run` | RR `test_camera_run_wires_inprocess_mode` |
| BUG-14 | S2 | Exception order: redis-py `TimeoutError` misreported as service down | `app/inference/client.py`: `TimeoutError` caught before `RedisError` | RR `test_inference_client_exception_order` |
| BUG-15 | S2 | One DB error kills a camera pipeline | `app/pipeline/camera_task.py`: per-frame `_handle_faces` try/except with session rollback | RR `test_handle_faces_survives_db_error` |
| BUG-16 | S2 | Hardcoded pipeline constants bypass config (0.55 / 300 s / 86400 TTL, grace windows) | `app/config.py`: 6 new settings (`alert_low_confidence`, `alert_dedup_window_s`, `absent_grace_minutes`, `late_grace_minutes`, `early_exit_minutes`, `camera_state_ttl_s`); camera task + alerts read them | RR `test_settings_expose_pipeline_policy_values` + alerts tests |
| BUG-17 | S2 | Duplicate detection used `recognition_threshold` (0.40) vs `duplicate_identity_sim` (0.90` elsewhere | `app/quality.py` uses `settings.duplicate_identity_sim` | RR `test_duplicate_face_detection_uses_identity_threshold` |
| BUG-18 | S2 | Alerts dedup mixes UTC timestamps with local dates | `app/services/alerts.py`: `_local_day_start_utc()` at all 3 call sites; grace windows from settings | RR `test_alert_dedup_window_uses_local_day_start` |
| BUG-19 | S3 | `TimeoutError` shadowing (documented) + local/server divergence | Shadowing fixed with BUG-14; **local-vs-server divergence left** → 06 | RR `test_inference_client_exception_order` |
| BUG-20 | S2 | `smoke_test.py` crashed on first assertion (`.json()` on HTML `/`) | Metadata check now `GET /api` | live: `scripts/smoke_test.py` exit 0 |
| BUG-21 | S3 | `_CONFIRM_BUFFER` keys never deleted (unbounded leak) | `camera_task.py`: stale-key sweep + release-on-confirm | RR `test_confirm_buffer_prune_sweep_and_release` |
| BUG-22 | S3 | `zip(jpegs, results)` silently truncates on short replies | Length mismatch logged (inference reply anomaly) | RR `test_confirm_reply_length_mismatch_is_logged` |
| BUG-23 | S3 | Latent `CameraOut` validation trap (500 risk on `GET /cameras`) | `app/schemas.py`: `CameraBase`/`CameraCreate`/`CameraUpdate`/`CameraOut` restructured | RR `test_camera_schema_rtsp_validator_*` |
| BUG-24 | S3 | WS auth checks signature only (revoked users keep access) | `app/api/ws.py`: full `resolve_principal` with DB session → `4401` | RR `test_ws_rejects_revoked_user` |
| BUG-25 | S3 | Legacy-token fallback mints super admin for unknown subjects | `app/security.py`: fail-closed → `401` | RR `test_token_with_unknown_subject_fails_closed` + live check |
| BUG-28 | S2 | Invalid `LOCAL_TIMEZONE` silently falls back to UTC | `app/timeutil.py`: logs a warning on invalid zone | RR `test_invalid_timezone_logs_warning` |
| BUG-29 | S3 | `replace_face` skipped duplicate-identity check | Guard added with `exclude_student_id`; `InferenceUnavailable` mapped | RR `test_replace_face_rejects_duplicate_identity` |
| BUG-30 | S4 | Debug `print()` in production paths (`enrollment`, `students`) | → `logger.debug`; tripwire test forbids `print(` in `app/` | RR `test_no_debug_print_statements_left_in_app_package` |
| SEC-02 (be) | M-H | Backend half of stored XSS: any `rtsp_url` scheme accepted | Pydantic validators: `rtsp://`, `rtsps://`, `http://`, `https://` only | RR schema tests + live `POST /cameras` → 422 |
| SEC-03 | M-H | Enrolled reference photos served **unauthenticated** at `/media` | `StaticFiles` mount replaced by authenticated, path-confined `GET /media/{file_path}` | RR `test_media_requires_auth_and_confines_path` + live 401/404 |
| SEC-08 | M | Deleted super admin can be reinstated via legacy token | Fixed with BUG-25 (fail-closed) | RR same as BUG-25 |
| SEC-09 | M | Bulk-import photo path traversal (suspected) | `app/services/enrollment.py` `_resolve_under()`: path confinement for CSV photo paths | RR `test_bulk_import_rejects_traversal_paths` |
| H-01 | — | 12 root ad-hoc scripts (incl. PII) | Deleted: `add_fallback.py`, `add_require_all.py`, `add_require_enforce.py`, `add_require_logic.py`, `apply_fix.py`, `fix_require_all.py`, `analyze_csv.py`, `list_students.py`, `test_drive.py`, `test_drive2.py`, `create_zip.py`, `build_handoff.py` (all verified unreferenced) | root `*.py` count = 0 |
| H-03 | — | `test_registration.py` hard-coded pattern | Rewritten against `settings.registration_no_pattern` + `_validate_reg_no` | 4 tests, pass |

## B. New defects discovered during repair (ran the code, found more)

| ID | Defect | Fix | Proof |
|---|---|---|---|
| BUG-31 | `add_photos` request validator (`3..5` photos per call) makes any top-up mathematically impossible once a student already has 3–5 photos (BUG-01's fix exposed it) | `validate_count` parameter: full validation on create, targeted append validation on top-up; empty append rejected with a clear message | RR `test_add_photos_appends…` (+ empty-photos case) |
| BUG-32 | `quality.check_duplicate_face` collapsed to 0-d via `squeeze()` with exactly one other student in the gallery → division `0/0` → NaN → duplicate detection silently returned `False` | valid-mask via `np.atleast_1d(all_norms.ravel() > 1e-8)`; gallery kept 2-D | RR `test_duplicate_face_single_row_gallery…`; BUG-29 test also depended on it |
| BUG-33 | Quality gate swallowed `InferenceServiceDown`/`InferenceTimeout` and reported **"no face detected" (400)** — an offline GPU service became the user's fault. Caught live: the repo's own `smoke_test.py` expected an honest 503 and failed | `app/quality.py` re-raises infra errors; `enrollment` converts to `InferenceUnavailable` (503) on the single-enroll path and to a per-row "inference service unavailable" rejection in bulk | 3 RR tests (`test_quality_gate_surfaces_service_outage`, `test_enroll_answers_503…`, `test_bulk_import_reports_outage_per_row…`) + live smoke now passes with `degraded 503` |

## C. Explicitly NOT fixed here

BUG-26 (RTSP transport is process-global), BUG-27 (events lose `0.0` ms /
datetime serialization), the `local.py` vs server behaviour divergence part of
BUG-19, and every security item listed in `07_NEXT_PHASE_SECURITY_PLAN.md`.
Reasons and plans: `06_REMAINING_ISSUES.md`.
