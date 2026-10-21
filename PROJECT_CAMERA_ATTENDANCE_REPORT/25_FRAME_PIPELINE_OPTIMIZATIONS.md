# 25 — Frame Pipeline Optimizations

**Date:** 2026-10-21 · **Scope:** smallest effective changes to the *existing*
OpenCV → Redis → MJPEG → browser path. No streaming framework was replaced,
no model was changed, no frontend redesign, no attendance/camera/DB logic
altered, no EXE rebuilt.

Each change below is justified by a number measured in report 24, and each is
covered by a regression test (report 27).

---

## 0. Decision record

| # | Question | Decision & evidence |
|---|---|---|
| 1 | Is transport the bottleneck? | **No.** MJPEG delivered 5.04 parts/s with `queue_len:0` and only ~44 KB/s. **Measure-first: kept MJPEG.** |
| 2 | Is GPU the bottleneck? | **No.** GPU 7–59 % (mean ≈ 41 %), host CPU 42 % of 28 threads. |
| 3 | Is decode the bottleneck? | **No.** 8.51 ms/frame → 73.3 FPS ceiling vs ~26 FPS source. |
| 4 | What is? | Display **waiting on inference** (§4.1), delivery **pacing/duplication** (§4.2), and **sampling_rate gating the picture** (§4.3). |
| 5 | Alternative transports (FFmpeg/PyAV/GStreamer/WebRTC/MediaMTX)? | **Not adopted** — no evidence the current path cannot meet the requirement after these fixes (measured 3.26× delivery gain, 4.06× publish gain). Re-evaluation criteria in report 28. |
| 6 | Re-export SCRFD for batched detection? | **Out of scope** (model surgery). Documented as the remaining GPU lever (report 28). |
| 7 | Overlay model | Labels are **baked into the JPEG** (no DOM overlay), so the annotated frame must still reach the browser. Solved by an explicit `X-Frame-Kind` protocol field rather than by dropping annotations. |

---

## 1. Display decoupled from inference — capture-thread display tap

**Where:** `app/config.py`, `app/redis_client.py`, `app/pipeline/frame_reader.py`,
`app/pipeline/camera_task.py`

The reader thread now taps decoded frames **for display only**:

```python
# frame_reader.py — inside the read loop, before the sampling skip
if self.on_display is not None:
    now_mono = time.monotonic()
    if now_mono - self._last_display_at >= self.display_interval_s:
        self._last_display_at = now_mono
        try:
            self.on_display(frame, ts)
            self.display_frames += 1
        except Exception:
            logger.exception("display tap failed for camera %s", self.camera_id)
if (index - 1) % self.sampling_rate != 0:
    continue                              # GPU path unchanged
```

* **Independent of `sampling_rate`** (recognition cadence) — the tap runs on
  every decoded frame, rate-limited by its own `live_publish_fps`.
* **Independent of inference latency** — no RPC, no DB, no batch in the path.
* **Paced on `time.monotonic()`**, *not* on the frame timestamp: a looping
  file source's media-timeline `ts` steps backwards at each reopen, which
  would have stalled the tap for ~15 s per loop.
* **Never breaks capture** — the callback is wrapped, failures are logged and
  swallowed, and `frames_read/frames_sampled` keep advancing.

New settings (`app/config.py`):

```python
live_publish_fps: int = 15     # 0 disables the tap (legacy behaviour)
live_mjpeg_poll_ms: int = 30   # delivery poll cadence
live_mjpeg_keepalive_s: float = 1.0
live_mjpeg_fps: int = 20       # was 6: now a CEILING, not a padded rate
```

The pipeline's closure (`camera_task.py`) does only the cheap display work —
measured 0.91 ms downscale + 0.34 ms encode:

```python
def _publish_display_frame(frame, ts):
    small = _annotate.downscale(frame, settings.live_frame_width)
    jpeg = _annotate.encode_jpeg(small, settings.live_frame_quality)
    if jpeg:
        rc.publish_live_frame_raw(r_bin, camera_id, jpeg,
                                  frame_ts=float(ts), ttl_s=settings.live_frame_ttl_s)
```

---

## 2. One atomic slot value: kind + timestamp + JPEG

**Where:** `app/redis_client.py`

A single `SET` now carries `{kind, frame_ts, JPEG}` so that the tap can write
the slot **without touching the inference status hash**, and so the stream can
report **each frame's own timestamp** (required to distinguish a new frame
from a repeated one):

```python
_FRAME_PREFIX_RAW = b"\x00"; _FRAME_PREFIX_ANNOTATED = b"\x01"
encode_live_frame(jpeg, *, frame_ts, annotated) -> prefix + "<d" + jpeg
decode_live_frame(blob) -> (jpeg, frame_ts | None, annotated)
publish_live_frame_raw(r_bin, camera_id, jpeg, *, frame_ts, ttl_s)   # slot only
publish_live_frame(...)                                              # slot + status hash, kind=annotated
```

* `read_live_frame()` strips the prefix → **snapshot and legacy MJPEG keep
  returning plain JPEG bytes** (a snapshot must remain a snapshot).
* `read_live_frame_ex()` returns `(jpeg, ts, annotated)` for the stream.
* **Legacy prefix-less blobs still decode** (raw, `ts=None` → caller falls
  back to the status hash), so a rolling deploy cannot produce garbage bytes.

Why not a second key or the status hash? Two keys would race (kind mismatching
the image); the status hash must stay authoritative for `infer_ok/fps/faces`,
which the display tap must never overwrite.

---

## 3. Change-driven MJPEG delivery

**Where:** `app/api/cameras.py` — `iter_live_parts`, `_live_part`, `camera_live`

| Before | After |
|---|---|
| read → yield → **sleep 200 ms**, always | poll every **30 ms** → yield **only when the frame timestamp changed** |
| duplicates always sent (84.3 % of reads) | `duplicate_share = 0.000` measured |
| up to 200 ms waiting per frame | mean part gap **61.1 ms** |
| no kind information | `X-Frame-Kind: raw \| annotated` header |
| `decr` on possibly-expired key → **−1** | clamp to ≥ 0 |

```python
changed = ts is None or ts != last_ts
within_gap = (now - last_emit) < min_emit_gap
if (not changed and (now - last_emit) < keepalive_s) or (within_gap and not (changed and annotated)):
    time_mod.sleep(poll_s); continue
```

* **`interval` and `max_parts` keep their original meaning** (max emit
  spacing / total part budget), so every existing test and caller is
  unaffected.
* **First part is emitted immediately** (existing contract).
* **Keep-alive re-send** (≥ `interval` and ≥ 1 s) distinguishes "quiet" from
  "dead" without padding the stream.
* **Annotated frames bypass the rate limit** — dropping them would have made
  the recognition overlay rare (measured: before this rule 12/149 annotated
  parts reached the browser; after, 34/149).
* The generator still **exits by itself when frames vanish** (no hang) and
  still **releases the viewer slot** in `finally`, now clamped at zero.
* Pacing floor fixed: `max(0.2, 1/6) → max(1/60, 1/live_mjpeg_fps)` (the
  0.2 s floor alone capped delivery at 5 parts/s). The legacy
  `/stream.mjpeg` direct-probe path keeps its 0.2 s floor — it is a
  connectivity probe, not the monitor.

---

## 4. Frontend: honest FPS, overlay-aware acceptance, resilient reconnect

**Where:** `app/static/app.js`

1. **`X-Frame-Kind` parsed** in `parseMonPart` (`annotated`, default `true`
   for legacy parts).
2. **Acceptance rule** (was: strictly newer only):

   ```javascript
   const newer = !last.ts || last.ts > mon.lastFrameTs;
   const overlay = !!last.annotated && mon.lastFrameTs > 0 && !!last.ts &&
     (mon.lastFrameTs - last.ts) * 1000 <= mon.overlayMaxLagMs;   // 600 ms
   if (!newer && !overlay) continue;
   mon.lastFrameTs = Math.max(mon.lastFrameTs, last.ts || 0);
   ```

   Fresh video always wins; an annotated frame may replace a *marginally*
   newer raw frame so the overlay actually lands on screen. It is always the
   frame its own labels belong to (no relabelling of a newer frame) and the
   picture never jumps backwards by more than 600 ms.
3. **Real displayed FPS** — `mon.frameTimes` (last 30 monotonic arrivals) →
   `monFpsText()` shows `live — 15.4 fps`. The status line no longer claims
   "live" without a rate behind it.
4. **Bounded exponential reconnect** — `scheduleMonRetry` now backs off
   3 s → 15 s (was a fixed 3 s), clears its own timer, and reports
   `Reconnecting in Ns — why`. **The transient-error dead-end is fixed**:
   a failed status pre-check now schedules a retry instead of returning with
   the monitor permanently dead (baseline defect §4.5).
5. **Picture-vs-recognition age** — when `|video_age_s − frame_age_s| ≥ 1 s`,
   `#mon-sub` shows `Picture 0.4s old (raw) · last recognition 8.1s old`, so
   "laggy video" and "slow inference" are finally distinguishable.
6. `live-status` gained `video_age_s` and `video_kind` (server side, in
   `app/api/cameras.py`), while `frame_age_s` keeps its original meaning
   (age of the recognised frame).

**Design preserved:** dark theme, monitor layout, `#mon-present`,
`#mon-events`, camera switching (still `monitorDisconnect` first — exactly one
reader), and the disconnected/reconnecting states.

---

## 5. What was deliberately NOT changed

* **`sampling_rate`, `BATCH_SIZE`, `BATCH_FLUSH_MS`, `JPEG_QUALITY`** — these
  tune recognition, not the picture, now that the picture is decoupled.
  Changing recognition cadence without physical cameras could not be validated.
* **Inference service / buffalo_l / SCRFD** — static-batch-1 limitation
  documented instead of re-exporting a model (report 28).
* **RTSP settings, camera FPS/GOP/exposure** — physical devices untouched.
* **Transport** — MJPEG retained (§0).
* **`live_frame_width=640` / `live_frame_quality=70`** — kept to preserve the
  existing visual design and bandwidth envelope; 1280×720 is quantified as an
  option in report 28 rather than applied blind.
* **Attendance/presence/dedup/WS/auth code paths** — untouched (report 27).
* **Desktop EXE** — not rebuilt (no frontend asset is packaged separately
  from the image, and the fix is server-side; release safety per Phase 10).

---

## 6. Change inventory

| File | Change |
|---|---|
| `app/config.py` | + `live_publish_fps`, `live_mjpeg_poll_ms`, `live_mjpeg_keepalive_s`; `live_mjpeg_fps` 6 → 20 |
| `app/redis_client.py` | + `struct` import, frame blob codec, `publish_live_frame_raw`, `read_live_frame_ex`; `publish_live_frame` marks `annotated`; `read_live_frame` strips framing |
| `app/pipeline/frame_reader.py` | + `on_display` / `display_interval_s` / `display_frames`, monotonic-paced tap, ts computed before the sampling skip, `display_frames` in the stop log |
| `app/pipeline/camera_task.py` | + display-tap closure + wiring into `FrameReader` |
| `app/api/cameras.py` | `_live_part`, change-driven `iter_live_parts` + keep-alive + viewer clamp, pacing floor fix, `video_age_s`/`video_kind` in `live-status` |
| `app/static/app.js` | kind parsing, overlay-aware acceptance, real FPS, backoff reconnect, transient-error retry, picture/recognition age |
| `tests/test_stream_performance.py` | **+18 regression tests (new file)** |
| `scripts/profile_frame_path.py` `profile_stream_delivery.py` `profile_publish_rate.py` | measurement harnesses (report 24) |

No existing test was modified, weakened, or deleted.
