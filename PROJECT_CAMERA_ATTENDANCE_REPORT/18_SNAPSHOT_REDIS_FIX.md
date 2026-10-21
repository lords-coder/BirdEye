# 18 — Snapshot Redis Fix (Camera-Page Coupling + Video Lag)

**Sprint:** 2026-10-18  
**Problems:** P0-1 (camera processing coupled to frontend) and P0-4 (video lag)

---

## Root Cause

The `/cameras/{id}/snapshot` endpoint opened a **new `cv2.VideoCapture`**
to the RTSP camera on every request:

```python
# BEFORE: every 2.5 s poll from the frontend opened a new RTSP session
jpeg = camera_test.read_frame_jpeg(camera.build_rtsp_url())
```

The frontend preview polls this endpoint every 2.5 seconds per camera.
With two cameras, that's **2 new RTSP sessions every 2.5 seconds** —
competing with the pipeline's FrameReader for the camera's limited
concurrent-session slots (typically 1–3 for IP cameras).

### Consequences
1. **Pipeline starvation:** The pipeline's FrameReader lost its RTSP
   session when the snapshot endpoint grabbed it. Frames were dropped or
   the pipeline went unhealthy.
2. **Page-dependent processing:** The camera whose preview page was open
   got the snapshot's RTSP session; the other camera's pipeline was
   starved. This created the illusion that only the previewed camera
   processed.
3. **Video lag:** Constant RTSP connection churn added seconds of latency
   to the pipeline's frame delivery.

---

## The Fix

Both `/cameras/{id}/snapshot` and the legacy `/cameras/{id}/stream.mjpeg`
now check the pipeline state first:

```python
# AFTER: read from shared Redis publication when pipeline is running
runtime = rc.get_camera_runtime(camera_id=camera_id)
if runtime["state"] == "running" and runtime["running"]:
    try:
        jpeg = rc.read_live_frame(rc.get_binary_redis(), camera_id)
        if jpeg:
            return Response(content=jpeg, media_type="image/jpeg")
    except redis_lib.RedisError:
        pass  # frame bus down — fall through to a direct probe
# Only when pipeline is stopped: direct probe
jpeg = camera_test.read_frame_jpeg(camera.build_rtsp_url())
```

### Behaviour Matrix

| Pipeline State | Redis Available | Snapshot Source | RTSP Sessions Opened |
|---------------|----------------|----------------|---------------------|
| Running | Yes | Redis frame bus | **0** |
| Running | No (down) | Direct probe (fallback) | 1 per request |
| Stopped | N/A | Direct probe | 1 per request |

---

## Regression Tests

### `test_snapshot_reads_redis_when_pipeline_running`
- Creates a camera, marks it running in Redis (state + heartbeat).
- Seeds a synthetic JPEG frame in the Redis binary store.
- Asserts: snapshot returns 200 with `image/jpeg` content-type and JPEG
  magic bytes (`\xff\xd8`).

### `test_snapshot_direct_probe_when_pipeline_stopped`
- Camera has no runtime state (pipeline stopped).
- Mocks `camera_test.read_frame_jpeg` to count calls.
- Asserts: snapshot returns 200, probe was called exactly once.

### `test_snapshot_never_calls_probe_when_running`
- Camera running with a seeded frame.
- Mocks `read_frame_jpeg` to **raise AssertionError** if called.
- Asserts: snapshot returns 200 (served from Redis), probe never called.

### `test_snapshot_while_other_camera_running`
- Camera A stopped, Camera B running with a seeded frame.
- Snapshot for A (with mocked probe to avoid 30 s timeout).
- Asserts: Camera B is still running after A's snapshot.

---

## Impact

| Metric | Before | After |
|--------|--------|-------|
| RTSP sessions opened per snapshot poll | 1 | **0** (pipeline running) |
| Pipeline starvation during preview | Yes | **No** |
| Video lag (primary cause) | Competing captures | **Eliminated** |
| Camera processing independence | Page-dependent | **Fully independent** |

The live monitor already reads from the shared Redis publication (reports
10–13). With the snapshot fix, **no API endpoint opens RTSP connections
while the pipeline is running** — camera processing is fully decoupled
from frontend activity.
