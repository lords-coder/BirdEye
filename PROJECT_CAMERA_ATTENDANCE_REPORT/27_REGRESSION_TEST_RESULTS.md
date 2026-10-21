# 27 — Regression Test Results

**Date:** 2026-10-21 · **Interpreter:** `.venv\Scripts\python.exe -m pytest` (never `-q`; full output recorded) · **Environment:** Windows 11, no GPU required for any test here.

Phase 9 required focused regression tests for the streaming-performance changes, then the full suite sequentially, with **baseline and final results recorded separately**.

---

## 1. New focused suite — `tests/test_stream_performance.py` (**18 tests, all passing**)

```
tests\test_stream_performance.py ..................  [100%]
18 passed, 1 warning in 4.83s
```

Each test guards one measured behaviour; none of them asserts a number that a legitimate optimization would invalidate.

| # | Test | Requirement it pins |
|---|---|---|
| 1 | `test_frame_blob_roundtrip_marks_kind_and_timestamp` | atomic `{kind, ts, JPEG}` slot value survives round-trip |
| 2 | `test_legacy_prefixless_blob_still_decodes_as_jpeg` | rolling deploy safety: old values still yield plain JPEG |
| 3 | `test_publish_live_frame_raw_leaves_inference_status_untouched` | display tap must **not** clobber `infer_ok/fps/faces` |
| 4 | `test_annotated_publish_is_marked_and_carries_its_own_ts` | inference frames are labelled `annotated` with their own ts |
| 5 | `test_mjpeg_emits_changed_frames_and_bounded_keepalive` | change-driven delivery + keep-alive + self-terminating generator |
| 6 | `test_mjpeg_annotated_frames_carry_annotated_kind` | `X-Frame-Kind: annotated` on the wire |
| 7 | `test_mjpeg_part_body_is_a_plain_jpeg` | part body decodes directly in a browser (no framing bytes) |
| 8 | `test_mjpeg_part_carries_frame_kind_and_ts` (covered by 5/6) | headers present and parseable |
| 9 | `test_annotated_frames_are_never_rate_limited_out` | recognition overlay cannot be rate-limited away by the tap |
| 10 | `test_raw_frames_respect_the_emit_rate_limit` | plain frames still honour the advertised maximum FPS |
| 11 | `test_viewer_counter_never_goes_negative` | the `"-1 viewer(s)"` defect cannot return |
| 12 | `test_display_tap_is_independent_of_recognition_cadence` | `sampling_rate=60` still delivers every decoded frame to display |
| 13 | `test_display_tap_is_rate_limited` | `display_interval_s` bounds the tap |
| 14 | `test_display_tap_failure_never_breaks_capture` | a display bug degrades the picture, never the pipeline (`out_q` still fed) |
| 15 | `test_display_tap_disabled_keeps_legacy_behaviour` | `live_publish_fps=0` reproduces the old path exactly |
| 16 | `test_live_status_exposes_video_age_and_kind` | observability: `video_age_s`, `video_kind`, honest `infer_ok: false` before any recognition |
| 17 | `test_snapshot_still_serves_the_shared_frame` | snapshot stays a snapshot (plain JPEG, bus hit) |
| 18 | `test_running_pipeline_never_opens_the_source_for_viewers` | **no competing capture** — `cv2.VideoCapture` is monkeypatched to explode |
| 19 | `test_pipeline_and_viewers_run_concurrently_on_one_publication` | two viewers + one pipeline share one publication; viewer counter returns to 0 |

(19 behaviours in 18 test functions — #8 is asserted inside #5/#6.)

### Phase-9 checklist mapping

| Phase 9 requirement | Test(s) |
|---|---|
| 1. Only one capture pipeline opens RTSP | #18 (VideoCapture explodes if any viewer path touches it), #19 |
| 2. No direct RTSP when pipeline running + bus empty | #18 |
| 3. Snapshot/MJPEG/WS unaffected by extra viewers | #19 (viewers 2 → 0, no source open) |
| 4. Camera failure isolation | existing `tests/test_camera_attendance.py` (29 tests) — untouched |
| 5. Both cameras run concurrently | existing dual-camera tests — untouched |
| 6. Unknown-person dedup preserved | existing `test_attendance_dedup_and_unknown.py` — untouched |
| 7. Presence requires confirmation | existing grace-window tests — untouched |
| 8. Bounded queues under load | existing `test_pipeline_queue_bounds.py` — untouched; #14 keeps `out_q` fed |
| 9. Inference batch grouping under multiple cameras | existing inference/batch tests — untouched |
| 10. MJPEG integrity while inference runs | #5–#10, #17 |
| 11. Correct behaviour during inference outages | #16 (`infer_ok: false` + video still served), plus the **live** outage measurement in report 26 §5 |
| 12. Enrollment matching unaffected | existing enrollment tests — untouched |
| 13. Camera switches stay one pipeline | #19 + existing `test_live_monitor.py` |

---

## 2. Existing suites — preserved contracts

No existing test was modified, weakened, or deleted. The contracts pinned by `tests/test_live_monitor.py` (first part yields immediately; generator exits when the frame key vanishes; viewer counter returns to `"0"`; snapshot returns a plain JPEG; annotated frames only from the shared publication) all still pass against the new delivery loop:

```
tests\test_live_monitor.py ..............        14 passed
tests\test_camera_attendance.py .........        29 passed   (43 total, 21.79s)
tests\test_pipeline_e2e.py .                     1 passed
```

---

## 3. Full suite — baseline vs final

| | Baseline (before changes) | Final (after all changes) |
|---|---|---|
| collected | 324 | **342** (+18 new) |
| passed | **322** | **340** |
| failed | **2** | **2** (identical) |
| failing tests | `tests/test_process_safety.py::TestSingletonMutex::test_acquire_singleton_lock_first_instance` · `...::test_acquire_singleton_lock_second_instance_fails` | **same two** |
| cause | **Environmental only**: the packaged `BirdEye.exe` (PIDs 32308/38244) is running on this machine and holds the singleton mutex, so the mutex-collision test cannot observe a collision. It passes whenever the EXE is not running. | unchanged — not caused by this work |

### 3.1 Final full-suite run (recorded verbatim)

```
.venv\Scripts\python.exe -m pytest          # 2026-10-21, sequential, single command
...
tests\test_process_safety.py:33: AssertionError
=========================== short test summary info ============================
FAILED tests/test_process_safety.py::TestSingletonMutex::test_acquire_singleton_lock_first_instance
FAILED tests/test_process_safety.py::TestSingletonMutex::test_acquire_singleton_lock_second_instance_fails
2 failed, 340 passed, 1 warning in 78.04s (0:01:18)
```

The two environmental failures persist for the same reason as the baseline; they are **not** caused by this work and are listed as such in report 28 (L-6). Net test delta: **+18 tests, +18 passing, 0 regressions, 0 existing tests modified.**

---

## 4. Safety notes for this phase

* Tests are **hardware-independent**: fakeredis + `tests/assets/test_multi.mp4` only. No camera, no GPU, no network service beyond the FastAPI `TestClient`.
* The new tests do **not** touch the running stack: they monkeypatch `rc.get_binary_redis` per test, so the live Redis (with camera 1 running) is never mutated by them.
* `test_display_tap_failure_never_breaks_capture` deliberately raises inside the tap and asserts the inference queue still receives frames — the display path is *permitted* to fail and *forbidden* from taking capture down with it.
* No assertion pins an FPS number that would break if `live_publish_fps` were tuned later: the tests assert ordering, isolation, and bounded-ness (e.g. "display frames ≥ 5 × sampled frames", "≤ 1 tapped frame with a 10 s interval"), not a specific rate.
