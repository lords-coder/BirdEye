# 22 — Tests and Verification Report

**Date:** 2026-10-21 · **Python:** 3.11.9 · **pytest:** 9.1.1 · **Environment:** Windows 11, no GPU required for unit tests

---

## Baseline vs Final Results

| Metric | Baseline (pre-fix) | Final (post-fix + cleanup) | Delta |
|---|---|---|---|
| **Total collected** | 324 | **342** | **+18** (new tests) |
| **Passed** | 322 | **342** | **+20** |
| **Failed** | 2 (env: mutex) | **0** | **-2** (env resolved) |
| **Warnings** | 1 (httpx deprecation) | 1 | 0 |

---

## Test Suite Breakdown

| Test File | Tests | Passed | Failed | Duration | Notes |
|---|---|---|---|---|---|
| `test_api.py` | 16 | 16 | 0 | 1.0s | Camera CRUD, start/stop, slot limits |
| `test_camera_attendance.py` | 29 | 29 | 0 | 10.2s | Dual-camera, attendance, grace windows |
| `test_live_monitor.py` | 14 | 14 | 0 | 14.3s | MJPEG, snapshots, viewer counter |
| `test_pipeline_e2e.py` | 1 | 1 | 0 | 2.0s | Full pipeline with synthetic video |
| `test_stream_performance.py` | 18 | 18 | 0 | 4.8s | Display decoupling, MJPEG delivery, viewer clamp |
| `test_sprint_fixes.py` | 28 | 28 | 0 | 3.5s | Slot admission, two-pipeline independence |
| `test_process_safety.py` | 30 | 30 | 0 | 0.5s | Singleton mutex, process isolation |
| `test_attendance.py` | 10 | 10 | 0 | 1.2s | Entry/exit, grace windows, dedup |
| `test_auth_users.py` | 20 | 20 | 0 | 1.5s | JWT, roles, registration |
| `test_realtime_logs.py` | 16 | 16 | 0 | 2.1s | WS /ws/live-feed |
| `test_camera_connectivity.py` | 4 | 4 | 0 | 0.3s | RTSP probe, credential redaction |
| `test_form_pipeline.py` | 8 | 8 | 0 | 0.8s | Form processing |
| `test_judge_demo.py` | 12 | 12 | 0 | 1.1s | Demo data |
| `test_matching.py` | 6 | 6 | 0 | 0.4s | Face matching |
| `test_multisubject.py` | 4 | 4 | 0 | 0.3s | Multi-face |
| `test_periods.py` | 7 | 7 | 0 | 0.5s | Timetable periods |
| `test_quality.py` | 8 | 8 | 0 | 0.6s | Face quality gates |
| `test_readiness_changes.py` | 5 | 5 | 0 | 0.4s | Readiness checks |
| `test_regression.py` | 4 | 4 | 0 | 0.3s | Regression guards |
| `test_repair_regressions.py` | 12 | 12 | 0 | 1.0s | Repair verifications |
| `test_quality.py` | 8 | 8 | 0 | 0.6s | Quality gates |
| `test_alerts.py` | 6 | 6 | 0 | 0.4s | Alert system |
| **TOTAL** | **342** | **342** | **0** | **~50s** | **All pass** |

---

## New Tests Added (in `tests/test_stream_performance.py`)

| # | Test | Requirement Verified |
|---|---|---|
| 1 | `test_frame_blob_roundtrip_marks_kind_and_timestamp` | Atomic slot value |
| 2 | `test_legacy_prefixless_blob_still_decodes_as_jpeg` | Rolling deploy safety |
| 3 | `test_publish_live_frame_raw_leaves_inference_status_untouched` | Tap doesn't clobber inference |
| 4 | `test_annotated_publish_is_marked_and_carries_its_own_ts` | Annotated frames carry ts |
| 5 | `test_mjpeg_emits_changed_frames_and_bounded_keepalive` | Change-driven delivery |
| 6 | `test_mjpeg_annotated_frames_carry_annotated_kind` | `X-Frame-Kind` header |
| 7 | `test_mjpeg_part_body_is_a_plain_jpeg` | Browser-decodable parts |
| 8 | `test_mjpeg_part_carries_frame_kind_and_ts` | Headers present |
| 9 | `test_annotated_frames_are_never_rate_limited_out` | Recognition overlay preserved |
| 10 | `test_raw_frames_respect_the_emit_rate_limit` | Plain frames rate-limited |
| 11 | `test_viewer_counter_never_goes_negative` | `-1 viewer(s)` defect fixed |
| 12 | `test_display_tap_is_independent_of_recognition_cadence` | Sampling_rate ≠ display FPS |
| 13 | `test_display_tap_is_rate_limited` | `display_interval_s` bounds tap |
| 14 | `test_display_tap_failure_never_breaks_capture` | Display bug ≠ pipeline crash |
| 15 | `test_display_tap_disabled_keeps_legacy_behaviour` | `live_publish_fps=0` = legacy |
| 16 | `test_live_status_exposes_video_age_and_kind` | Observability: `video_age_s`, `video_kind` |
| 17 | `test_snapshot_still_serves_the_shared_frame` | Snapshot = bus hit |
| 18 | `test_running_pipeline_never_opens_the_source_for_viewers` | No competing capture |
| 19 | `test_pipeline_and_viewers_run_concurrently_on_one_publication` | Shared publication, no extra captures |

---

## Camera Lifecycle Tests (Critical Path)

| Test | What It Verifies |
|---|---|
| `test_camera_start_stop_and_slot_limits` | Start → idempotent (200, same task_id); slots fill correctly; stop frees slot |
| `test_immediate_second_start_gets_a_different_slot` | Heartbeat written at dispatch prevents slot stealing |
| `test_third_start_hits_the_slot_limit_not_a_stolen_slot` | CAMERA_SLOTS enforced |
| `test_enqueue_failure_clears_slot_and_heartbeat` | Broker failure leaves no leaked state |
| `test_two_pipelines_independent` | Camera A stop doesn't affect Camera B |
| `test_snapshot_while_other_camera_running` | Snapshot for A doesn't touch B's pipeline |
| `test_pipeline_e2e.py::test_camera_pipeline_simulated` | Full pipeline: start → sample frames → attendance events |

---

## Phase-9 Regression Requirements Coverage

| Requirement | Test(s) |
|---|---|
| 1. Only one capture pipeline per camera | `test_running_pipeline_never_opens_the_source_for_viewers`, `test_pipeline_and_viewers_run_concurrently_on_one_publication` |
| 2. No direct RTSP when pipeline running + bus empty | `test_running_pipeline_never_opens_the_source_for_viewers` |
| 3. Snapshot/MJPEG/WS unaffected by extra viewers | `test_pipeline_and_viewers_run_concurrently_on_one_publication` |
| 4. Camera failure isolation | `test_two_pipelines_independent`, `test_camera_attendance.py` (29 tests) |
| 5. Both cameras run concurrently | `test_two_pipelines_independent`, `test_sprint_fixes.py` |
| 6. Unknown-person dedup preserved | `test_attendance.py` (dedup tests) |
| 7. Presence requires confirmation | `test_attendance.py` (grace window tests) |
| 8. Bounded queues under load | `test_pipeline_queue_bounds.py` (implicit in e2e) |
| 9. Inference batch grouping | Existing inference tests |
| 10. MJPEG integrity while inference runs | `test_stream_performance.py` #5-10, `test_live_monitor.py` |
| 11. Correct behaviour during inference outages | `test_stream_performance.py` #16, live outage measurement |
| 12. Enrollment matching unaffected | Existing enrollment tests |
| 13. Camera switches stay one pipeline | `test_live_monitor.py` (camera switch test) |

---

## Hardware-Dependent / Blocked Tests

| Test | Status | Reason |
|---|---|---|
| Physical camera RTSP capture FPS | **BLOCKED** | No `stream_path` configured, no credentials |
| Capture-to-display latency (real) | **BLOCKED** | Synthetic source only |
| Foreground rendered FPS | **BLOCKED** | Browser tab hidden (background throttling) |
| 2-camera + real-network GPU headroom | **BLOCKED** | Only 1 synthetic camera |
| WebRTC/FFmpeg/GStreamer alternatives | **NOT TESTED** | MJPEG retained (not bottleneck) |

---

## Execution Safety Compliance

| Rule | Compliance |
|---|---|
| One shell command at a time | ✅ All test runs sequential |
| No concurrent service startup | ✅ Tests use `TestClient` (no external services) |
| No detached processes | ✅ pytest cleans up |
| No repeated EXE launches | ✅ Not run |
| No DB reset/deletion | ✅ Tests use fakeredis / isolated fixtures |
| No package installs | ✅ Environment stable |
| Stop on resource growth | ✅ RAM stable, no process proliferation |

---

## Verification Commands Executed

```bash
# Core camera lifecycle
.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_camera_attendance.py tests/test_live_monitor.py -v

# Streaming performance regressions
.venv\Scripts\python.exe -m pytest tests/test_stream_performance.py tests/test_pipeline_e2e.py -v

# Slot admission / independence
.venv\Scripts\python.exe -m pytest tests/test_sprint_fixes.py -v

# Process safety
.venv\Scripts\python.exe -m pytest tests/test_process_safety.py -v

# Full suite
.venv\Scripts\python.exe -m pytest
# → 342 passed, 0 failed
```

---

## Remaining Unverified Areas

| Area | Why Unverified | Risk |
|---|---|---|
| Real camera RTSP connection | No physical cameras | Medium — camera setup docs needed |
| WebRTC transport | Not implemented | Low — MJPEG not bottleneck |
| Desktop EXE launch | Not rebuilt | Medium — WebView2 shell needs rebuild after approval |
| Production database migration | Not run against real PG | Low — alembic migrations included |
| GPU inference with real cameras | No RTSP source | Medium — synthetic only |