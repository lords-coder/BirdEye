# 15 — Critical Bugs and Root Causes

**Sprint:** 2026-10-18  
**Baseline:** 237 tests passing, 0 failed  
**After fixes:** 250 tests passing, 0 failed (13 new sprint tests)

---

## Summary

Four production-critical problems were reported. Each was traced to a
specific code path, fixed, and covered by regression tests.

| ID | Problem | Root Cause | Fix |
|----|---------|-----------|-----|
| P0-1 | Camera processing coupled to frontend page | `/cameras/{id}/snapshot` opened a **new RTSP capture every 2.5 s**, starving the pipeline's FrameReader | Snapshot reads from Redis when pipeline running |
| P0-2 | Entry/exit mapping / corridor passersby | State machine was correct; missing regression tests for all scenarios | Added corridor-passersby and entry/exit tests |
| P0-3 | Detection-log flood (2147 unknown rows) | `_unknown_hash()` hashed raw float embeddings that changed every frame → dedup key never repeated | Quantized hash to 2 dp; all dimensions hashed |
| P0-4 | Live-video lag | Same competing RTSP captures as P0-1 | Fixed by P0-1 fix |

---

## P0-1: Camera Processing Coupled to Frontend Page

### Symptom
Only the camera whose preview page was open processed frames; the other
camera's pipeline appeared to stop.

### Root Cause
`/cameras/{id}/snapshot` called `camera_test.read_frame_jpeg()` which opens
a **new `cv2.VideoCapture`** to the RTSP camera on every request. The
frontend preview polls this endpoint every 2.5 s, so a second RTSP session
was constantly competing with the pipeline's FrameReader for the same
camera. Most IP cameras allow only 1–3 concurrent RTSP sessions; the
pipeline's session was starved and frames were dropped or the pipeline
went unhealthy.

### Evidence
- `app/api/cameras.py` line ~437: `camera_snapshot` called
  `camera_test.read_frame_jpeg(camera.build_rtsp_url())` unconditionally.
- `app/api/cameras.py` line ~474: legacy `camera_stream` also opened a
  direct `cv2.VideoCapture` per HTTP connection.

### Fix
Both endpoints now check `rc.get_camera_runtime()` first. When the
pipeline is running (`state == "running"` and heartbeat fresh), they read
the shared frame from Redis (`rc.read_live_frame`) — **no RTSP connection
is opened**. A direct source probe is used only when the pipeline is
stopped, so an admin can still verify connectivity before starting it.

---

## P0-2: Entry/Exit Mapping and Corridor Passersby

### Symptom
Concern that corridor passersby might count as "inside" and inflate
attendance.

### Root Cause
The presence state machine in `app/services/presence.py` was already
correct:
- `classroom_interior` camera → entry evidence only
- `doorway_exterior` camera → exit evidence only
- Corridor sighting with no open session → `NEEDS_REVIEW` (not INSIDE)

However, there were **no regression tests** covering the corridor-passersby
scenario, so the behaviour was unverified.

### Fix
Added `TestCorridorPassersby` in `tests/test_sprint_fixes.py`:
- `test_corridor_only_creates_review_not_inside`: exterior sighting with
  no session produces `review_event=True`, state is not `INSIDE`, no
  `AttendanceSession` is invented, and a `PresenceEvent` with
  `review_status="open"` is created.
- `test_interior_creates_inside`: interior sighting produces `INSIDE`.

---

## P0-3: Detection-Log Flood (Unknown Faces)

### Symptom
2147 unknown-face `RecognitionLog` rows in the production database (vs 12
known rows). The monitoring panel was flooded with unknown-face alerts.

### Root Cause
`_unknown_hash()` in `app/pipeline/camera_task.py` hashed the **raw float
embedding** (first 16 dimensions) via JSON serialization. Real embeddings
vary slightly every frame due to lighting, angle, and JPEG noise, so the
hash was different on every frame. The Redis dedup key
`unknown:{hash}` therefore never matched, and every frame wrote a
`RecognitionLog`, published an event, and created an alert.

### Evidence
- `Total=2159  Known=12  Unknown=2147` in the production DB.
- `_unknown_hash` used `list(embedding[:16])` — only the first 16 of 512
  dimensions, and no quantization.

### Fix
`_unknown_hash` now:
1. **Quantizes** all values to 2 decimal places (`round(float(x), 2)`),
   so the same person produces the same key across frames.
2. **Hashes all dimensions** (not just a prefix), so faces differing only
   in later dimensions still get distinct keys.

### Verified Behaviour
- 20 jittered frames of the same unknown face → **1 event, 1 log row,
  1 alert**.
- Two distinct unknown faces → **2 separate events**.

---

## P0-4: Live-Video Lag

### Symptom
Live monitor feed was laggy (seconds behind real time).

### Root Cause
Same competing RTSP captures as P0-1. Each snapshot request opened a new
`cv2.VideoCapture`, adding latency to the pipeline's FrameReader. The
pipeline could not keep up with the extra connection churn.

### Fix
Eliminated by the P0-1 fix. The snapshot endpoint no longer opens RTSP
connections when the pipeline is running. The live monitor reads from the
shared Redis publication (25–30 fps verified in report 13).

---

## Files Changed

| File | Change |
|------|--------|
| `app/pipeline/camera_task.py` | `_unknown_hash` quantized + full-dimension hash |
| `app/api/cameras.py` | `camera_snapshot` and `camera_stream` read from Redis when pipeline running |
| `app/config.py` | Added `unknown_cooldown_s` setting (safety net) |
| `tests/test_sprint_fixes.py` | 13 new regression tests |

---

## Test Results

| Suite | Before | After |
|-------|--------|-------|
| Full suite | 237 passed | **250 passed** |
| New sprint tests | — | 13 passed |
| Regressions | — | **0** |
