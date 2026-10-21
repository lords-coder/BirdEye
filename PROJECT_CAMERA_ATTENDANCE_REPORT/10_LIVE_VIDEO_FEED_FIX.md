# Live Video Feed Fix (10)

## Root cause

**"Camera reports RUNNING but no video appears in the browser"** — two
independent defects:

1. **No live video transport existed.** The `/cameras/{id}/stream.mjpeg`
   endpoint opened a *separate* OpenCV capture per HTTP connection, was not
   connected to the recognition pipeline at all, and returned raw frames
   without overlays. The frontend's "preview" button polled
   `/cameras/{id}/snapshot` — a separate single-frame probe that opens its
   own capture every 2.5 s. Neither path used the pipeline's frames, so
   what the camera pipeline processed was never shown to the browser, and
   what the browser showed was a different, unannotated capture.

2. **Worker thread pool crash on Windows.** When the pipeline task for a
   credential-less RTSP camera failed and the API sent a Celery
   `revoke(terminate=True)`, Celery's `ThreadPool` raised
   `NotImplementedError: does not implement kill_job` and killed the
   worker process. The camera state key lingered in Redis as `"running"`,
   so the API reported RUNNING forever with no worker actually processing
   frames.

## Fix

- **Shared frame bus** (`app/redis_client.py`): `publish_live_frame()`
  writes the latest annotated JPEG to `cam:{id}:frame` (binary, 15 s TTL)
  plus a status hash (`cam:{id}:live`) with timestamp, FPS, inference
  health, face count, error. All viewers read the same frame — N viewers
  never create N captures.
- **Pipeline publishes annotated frames** (`app/pipeline/camera_task.py`):
  after each batch is inferred, the last frame is annotated and published.
  The overlay reuses the batch's existing detection+embedding results —
  no second inference call.
- **New endpoint** (`app/api/cameras.py`):
  - `GET /cameras/{id}/live.mjpeg` — multipart MJPEG from the shared
    publication, header-only auth, per-part `X-Camera-Id` / `X-Frame-Ts`.
    Returns 409 if the pipeline is stopped, 503 if running but no frame yet.
  - `GET /cameras/{id}/live-status` — JSON: state, has_frame, frame_age_s,
    fps, infer_ok, faces, error, viewers.
- **Frontend** (`app/static/app.js`): new "Monitor" section with camera
  selector, Start/Stop/Retry controls, streaming `<img>` via `fetch` +
  `ReadableStream` (header auth, no query token), stale-frame detection,
  reconnect with backoff, present-now panel, and per-camera event feed.
  Logout and section-switch abort the stream cleanly.

## Files changed

| File | Change |
|---|---|
| `app/redis_client.py` | `get_binary_redis()`, `cam_frame_key`, `cam_live_key`, `publish_live_frame`, `read_live_frame`, `read_live_meta` |
| `app/pipeline/annotate.py` | **new** — bbox + label drawing, downscaling, JPEG encoding |
| `app/pipeline/camera_task.py` | publishes annotated frame after each batch; flags inference outages |
| `app/api/cameras.py` | `live.mjpeg` endpoint, `live-status` endpoint, `_camera_viewer_header`, `iter_live_parts` |
| `app/static/index.html` | Monitor section markup, nav button |
| `app/static/app.js` | Monitor JS (streaming client, status polling, present panel, event feed) |
| `tests/test_live_monitor.py` | **new** — 13 tests covering the full feed path |