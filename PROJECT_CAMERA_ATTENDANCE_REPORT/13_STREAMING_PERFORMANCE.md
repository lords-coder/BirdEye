# Streaming Performance (13)

## Measured (file-source camera, RTX 5060, CUDA inference)

| Metric | Value |
|---|---|
| Pipeline FPS (frames/s processed) | 25–30 |
| Live MJPEG delivery FPS | ~5 (paced by `live_mjpeg_fps=6`) |
| Frame dimensions delivered | 640×640 (downscaled from source) |
| Frame TTL in Redis | 15 s (`live_frame_ttl_s`) |
| Frame age at fetch time | < 3 s |
| Active viewers counter | tracked per camera (`cam:{id}:viewers`) |

## Resource behaviour

- **One capture per camera.** The pipeline's `FrameReader` thread owns the
  single `cv2.VideoCapture`. All viewers read from the same Redis key —
  N browser tabs never open N RTSP connections.
- **No backlog.** Only the latest frame is stored (`cam:{id}:frame`, TTL
  15 s). A slow viewer reads the latest, never a queue.
- **Generator lifecycle.** Each HTTP stream is a bounded generator
  (`max_parts = live_mjpeg_fps × 600`). It exits when the client
  disconnects (send-side error) or when frames vanish (source died). The
  `finally` block always releases the viewer slot counter.
- **Inference failure is explicit.** When the RPC fails, the pipeline
  publishes an empty JPEG with `infer_ok=False` and an error string; the
  last good frame ages out on its TTL. The UI shows "inference
  unavailable" rather than a frozen frame masquerading as live.
- **No stale frames on camera switch.** Each part carries `X-Camera-Id`;
  the client discards any part whose ID differs from the currently
  selected camera.
- **Thread pool safety.** The pipeline runs inside Celery's threads pool.
  Camera stop uses the stop-flag path (polled every ~250 ms), not
  `revoke(terminate=True)` which crashes the Windows ThreadPool.

## Limitations

- Delivery is paced at 5 fps regardless of pipeline throughput; a
  30 fps stream is not delivered frame-for-frame to the browser.
- The Redis frame key is a single latest-frame slot; if the Redis
  connection drops, the stream ends and the UI shows "event bus
  unavailable" (the WebSocket recovery path handles reconnection).
- RTSP camera latency depends on the camera's own encoding delay; the
  system's contribution is the batch window (`batch_flush_ms=120 ms`) plus
  one inference round-trip (~250 ms).