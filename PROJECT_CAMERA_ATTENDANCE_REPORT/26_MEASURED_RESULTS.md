# 26 — Measured Results (Before / After)

**Date:** 2026-10-21 · **Source:** synthetic main stream (`/samples/videos/perf_baseline.mp4`, 2560 × 1440 @ 26 FPS, camera id 1, `sampling_rate=5`) · **Physical-camera figures: BLOCKED.**

Baseline numbers come from report 24 (code unmodified). "After" numbers were taken from the rebuilt image (built **14:29:35 IST**, deployed **14:41**, measurements **14:58–15:05**, i.e. after the inference service reported `online` and the pipeline had recovered). Every figure is a script output; commands are listed in §5.

> **Measurement honesty note.** The synthetic file source is *not wall-clock paced*: OpenCV decodes it as fast as CPU allows, so absolute frame rates are **upper bounds**. All ratios (before → after) use the same source, same camera row, same sampling rate, and the same scripts, so the comparison is valid. Timestamps on file sources follow the media timeline, which is why `frame_age_s` / `video_age_s` are **negative** in the raw output (§ report 24 §1).

---

## 1. Headline before/after

| Metric | Baseline (report 24) | After (final pass, §7) | Change |
|---|---|---|---|
| **Distinct frames published to Redis / s** (display) | **3.88** | **15.75** | **4.06 ×** |
| **MJPEG parts delivered to browser / s** | **5.04** | **16.69** | **3.31 ×** |
| — distinct frame timestamps delivered / s | ~3.88 | **16.17** | **4.17 ×** |
| — duplicate parts on the wire | ~23 % of parts | **0.5 %** (194/195 distinct) | eliminated |
| **Mean part gap** (browser inter-frame) | **201.8 ms** | **60.2 ms** | **3.4 × lower** |
| Max part gap | 219.0 ms | 94.0 ms | **2.3 × lower** |
| **Annotated (recognition) frames delivered / s** | ~5.04 (all parts) | **2.5** (30/195 = 15.4 %) | **preserved**, now additive |
| **Video during inference outage** | **frozen → `has_frame:false` → HTTP 503** | **14.81 FPS, 149 parts / 10 s** | decoupled |
| **Snapshot latency** (bus hit, 20-burst) | mean **19.7 ms** (p50 20.1) | mean **17.0 ms** (p50 13.1) | still a bus hit, no source open |
| **Viewer counter** | rendered **"-1"** / **"-2"** | clamped **≥ 0** | defect fixed |
| **Server-side `infer_ms`** (det+emb, 8 frames) | mean **133.8** (max 144.8) | mean **132.2** (max 138.1) | **unchanged — recognition not slowed** (§7.1) |
| `det_ms` / `emb_ms` (inference logs) | 105–121 / 14–17 ms | **115–119 / 16 ms** | unchanged |
| **GPU util** (one camera) | 7–59 %, mean **40.7 %** | 5–54 %, mean **24.2 %** | not saturated either way (§6.1) |
| Per-viewer bandwidth | ~44 KB/s | ~119 KB/s | 2.7× (real frames, not duplicates) |

---

## 2. Display publish rate — `scripts/profile_publish_rate.py`

Sampling the same Redis key the MJPEG stream serves, 198 reads / 8.0 s @ 40 ms:

| | Baseline | After |
|---|---|---|
| snapshot reads | 198 | 198 |
| **distinct frames** | **31** | **126** |
| **display_publish_fps** | **3.88** | **15.75** |
| duplicate reads | 167 (84.3 %) | 72 (36.4 %)* |
| snapshot latency mean / max | 20.3 / 40.5 ms | 24.3 / 46.2 ms |

\* The reader polls at 25 reads/s while the publisher runs at 15.75 frames/s, so re-reads of an unchanged key are expected in this probe; on the **MJPEG wire** the change-driven emitter filters them entirely (`duplicate_share = 0.000`, §3).

---

## 3. Delivery to the browser — `scripts/profile_stream_delivery.py` + `--kinds`

### 3.1 Full run (15 × 1 s status samples, 12 s MJPEG window, 20-snapshot burst)

| | Baseline | After |
|---|---|---|
| delivered parts | 61 / 12.11 s | **197 / 11.98 s** |
| **delivered_fps** | **5.04** | **16.44** |
| **mean part gap** | **201.8 ms** | **61.1 ms** |
| bytes in window | 534 080 | ≈ 1.3 MB |
| inference throughput `fps` (frames ÷ RPC-s) | 35.7 mean | 28.05 mean (24.6–31.52) |
| `infer_ms` | 133.8 mean / 144.8 max | 187.4 mean / 219.4 max |
| snapshot burst mean / p50 / max | 19.7 / 20.1 / 35.2 ms | 25.1 / — / — ms |
| snapshot verdict | "frame-bus hit (no source open)" | same |

### 3.2 Frame-kind mix on the wire (12 s)

| | Before the kind rule | After |
|---|---|---|
| parts | 149 | 199 |
| raw parts | 0 | 165 |
| **annotated parts** | 149 (100 %) | **34 (17.1 %) → 2.83 / s** |
| **distinct frame ts** | — | **199 (100 %)** |
| **distinct_fps** | — | **16.52** |
| duplicate_share | — | **0.000** |

Before the emitter rule ("annotated frames bypass the emit-rate gap") the same probe measured **12/149 = 8.1 %** annotated parts — i.e. the tap's faster publishes were *dropping* 2 of every 3 recognition frames. That is exactly the regression the Phase-8 requirement ("never attach outdated identity labels", "recognition overlays preserved") forbids; the rule restored it to 17.1 % ≈ 2.8 overlays/s against a pipeline cadence of ~3.5 batches/s.

### 3.3 Frame age observability (new `live-status` fields)

```
before: { "has_frame": true, "frame_age_s": 0.1,  "fps": "34.01", ... }          ← one number, ambiguous
after : { "has_frame": true, "frame_age_s": -15.3, "video_age_s": -0.7,
          "video_kind": "raw", "fps": "34.01", "infer_ok": true, ... }
```

`video_age_s` (age of the picture) and `frame_age_s` (age of the last recognised frame) are now reported separately, and `video_kind` says whether the newest slot value is a plain capture or a recognition-annotated frame. The UI surfaces the split only when the two differ by ≥ 1 s: `Picture 0.4s old (raw) · last recognition 8.1s old`.

---

## 4. Inference-side `det_ms` excursion — **observed at 15:00, resolved at 15:45**

| Window | `det_ms` (8 frames, 8 faces) | `emb_ms` | `total_ms` | `infer_ms` mean (live-status) |
|---|---|---|---|---|
| Baseline (12:40, tap absent) | 104.9 – 120.5 | 13.7 – 16.8 | 123.1 – 140.2 | **133.8** |
| Excursion (14:59, tap present) | 151.2 – 186.2 | 20.0 – 31.5 | 183.4 – 218.7 | **187.4** |
| **Final pass (15:45, tap present)** | **115.5 – 119.0** | **15.7 – 16.6** | **134.2 – 139.4** | **132.2** |

`frames` / `faces` / `batched_det` / `queue_len` were **8 / 8 / false / 0 in all three windows** — identical workload. The inference service is untouched by this work.

The 14:59 excursion is **transient ambient load/thermal**, not the display tap, because the same containers with the same tap setting returned to baseline-level latency 45 minutes later (final pass). Supporting evidence:

* the display tap's entire measured cost is **0.91 ms downscale + 0.34 ms encode × 15 fps ≈ 19 ms of worker CPU per second** — it never touches the inference service, the model, or the RPC payload (same 8 frames, same JPEG quality, same batch size);
* host conditions during the excursion: GPU 69–75 °C on a laptop part after ~2 h of additional unrelated GPU load (games/browser video), inference container freshly recreated after a model re-download with a cold CUDA kernel cache (`warmup_ms` 56 s → 31 s);
* conclusion: **recognition was not slowed by the optimization** (`infer_ms` 132.2 vs 133.8 baseline, `det_ms` 115–119 vs 105–121).

A controlled A/B (`LIVE_PUBLISH_FPS=0` → compare) was attempted and interrupted for an unrelated reason — changing `.env` also invalidated the inference container's config, triggering another full model re-download (L-5) before the tap-off window could be measured. It was superseded by the final-pass evidence above; the interruption itself is recorded in report 28 §L-3 for honesty.

---

## 5. Inference-outage behaviour (Phase 9, requirement 11) — measured

During the model warm-up window the pipeline held `infer_ok: false`, `fps: "0.0"`, `error: "inference unavailable (6 consecutive failures)"`:

| | Baseline behaviour | Measured after |
|---|---|---|
| `has_frame` | `false` after TTL | **`true`** |
| delivered FPS | **0** (stream ended / 503) | **14.81** (149 parts / 10 s) |
| mean part gap | n/a | **68.1 ms** |
| snapshot during outage | 503 | **12.6 ms**, bus hit |
| `video_kind` | n/a | `raw` (capture tap, no overlay yet) |
| recovery | n/a | pipeline auto-recovered to `infer_ok: true` within one retry cycle once the service returned; no restart of worker/API/DB |

This is the same defect class that made the original monitor show a frozen frame and then a permanently dead status line; both are now observable and recoverable.

---

## 6. Resource cost of the change

| | Baseline | After |
|---|---|---|
| GPU util (one camera) | 7–59 %, mean ≈ 41 %, 2433–2444 MiB, 69–75 °C | see §6.1 |
| Host CPU (i7-14700HX, 20C/28T) | 42 % | 42 % |
| worker container CPU | not captured | **289 %** (≈ 2.9 of 28 threads) |
| api container CPU | not captured | **9.4 %** |
| inference container CPU | not captured | **78 %** |
| API memory | — | 133 MiB / 7.6 GiB |
| worker memory | — | 815 MiB / 7.6 GiB |

The worker figure is dominated by the **unpaced file source** (decode ~117 FPS + full-res q85 encode of 8 frames per batch); on a 26 FPS RTSP camera the same pipeline decodes 26 FPS, i.e. roughly **5× less decode work** than measured here. The 19 ms/s tap cost (§4) is < 2 % of one core.

### 6.1 GPU re-measurement with the optimized path (final pass, 15:45)

`nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu,power.draw`, 6 × 1 s samples, one camera streaming at full rate + one MJPEG viewer attached:

| t | GPU util | memory | temp | power |
|---|---|---|---|---|
| 15:45:24 | 7 % | 3097 MiB | 69 °C | 26.3 W |
| 15:45:25 | 54 % | 3097 MiB | 72 °C | 33.5 W |
| 15:45:26 | 11 % | 3097 MiB | 69 °C | 32.2 W |
| 15:45:27 | 47 % | 3090 MiB | 72 °C | 31.8 W |
| 15:45:28 | 21 % | 3090 MiB | 70 °C | 32.7 W |
| 15:45:29 | 5 % | 3088 MiB | 69 °C | 30.2 W |

Baseline for comparison (report 24 §3.7): 59/42/37/41/7/58 % → mean **40.7 %**, 2433–2444 MiB, 69–75 °C, 32 W. After: mean **24.2 %** (5–54 %), 3088–3097 MiB, 69–72 °C, 26–33 W. **The GPU is not saturated in either state**, and delivering 3.3× more frames to the browser did not raise GPU load (display work is CPU-side: 0.91 ms downscale + 0.34 ms encode per tapped frame). Memory +650 MiB is shared-adapter noise from other GPU consumers on the host (browser video decode, games), not the pipeline — the inference container itself reports 1.98 GiB in both states.

---

## 7. Final confirmation pass (completed 15:43–15:46)

The stack was re-deployed a second time during the A/B attempt (L-3), so a final measurement set was taken after the inference service reported `online` at **15:43:09**, with camera 1 still running from the outage test — **no restart of worker/API/DB, no camera restart, no state surgery**. The pipeline had **auto-recovered** from the outage on its own.

| Check | Result |
|---|---|
| pipeline recovered after outage | ✅ `infer_ok_all: true` over 15/15 samples, `faces: "1"`, `error: null` |
| delivered MJPEG | **16.69 parts/s** (200 / 11.99 s), mean gap **60.2 ms**, max 94.0 ms |
| bandwidth | 1 430 934 B / 12 s ≈ **119 KB/s** per viewer |
| snapshot burst (20) | mean **17.0 ms**, p50 13.1, max 32.4 — *frame-bus hit* (baseline 19.7 ms) |
| frame-kind mix (12 s) | 195 parts → 165 raw + **30 annotated (15.4 %)**, distinct ts 194/195, **duplicate_share 0.005** |
| inference throughput | `fps` mean 34.4–37.6, `infer_ms` mean **132.2** (max 138.1) |
| inference service logs | `det_ms` **115.5–119.0**, `emb_ms` **15.7–16.6**, `total_ms` 134.2–139.4, `frames:8`, `faces:8`, `batched_det:false`, `queue_len:0` |
| GPU | §6.1 — 5–54 %, not saturated |
| container CPU | worker 294 %, api **4.8 %**, inference 81 % |
| container memory | worker 845 MiB, api 133 MiB, inference 1.98 GiB (host total 15.7 GB) |

**Host memory at end of the run:** 0.8 GB free of 15.7 GB. Attributed, not guessed: `vmmemWSL` (the WSL2 VM backing Docker Desktop) holds **4.84 GB** — normal for this stack, since the inference model alone occupies 1.98 GiB inside it and the rebuilt image is 24.1 GB — and the pre-existing Opera GX browser holds ~3.8 GB across 8 processes. Process counts were **unchanged** across the whole run (6 × `python.exe`, all started at 12:56 by the packaged `BirdEye.exe` launcher — two each of `app.inference.server`, `celery`, `uvicorn`; plus the 2 pre-existing `BirdEye.exe`), i.e. **no process proliferation and no orphans from this work**. Nothing unrelated was terminated; container CPU at the end was 0.03–0.09 %.

### 7.1 `det_ms` attribution — now resolved

The 187.4 ms mean seen at 15:00 was **transient ambient**, not the display tap: the same containers, same tap setting, 45 minutes later report `infer_ms` mean **132.2** and per-batch `det_ms` 115–119 — statistically identical to the 133.8 / 105–121 baseline. Combined with §4 (the tap adds ~19 ms of worker CPU per second and never touches the inference service), the conclusion is: **the optimization did not slow recognition.** The A/B in report 28 §L-3 is therefore closed by this measurement rather than by the interrupted experiment, and L-3 is updated accordingly.

### 7.2 Real-browser verification (Phase 5) — new frontend bundle in a live tab

The monitor tab had been open since before the redeploy and was still executing the **old** `app.js` (`monFpsText is not defined`), so it was reloaded and re-pointed at camera 1. With the deployed bundle and camera 1 streaming:

| Observed in the DOM | Value |
|---|---|
| `mon.frames` (frames applied to the `<img>`) | 16 → 105 and rising |
| status line | **`live — 1.4 fps`** (was a bare `live`) |
| sub line | **`Picture -7.2s old (raw) · last recognition -0.2s old`** |
| viewers badge | `3 viewer(s)` (matches Redis) |
| `#mon-events` rows | 40 (camera/face events flowing) |
| `mon.present` | 0 — **expected**: the Docker DB has no enrolled students, so a detected face has nothing to match against; enrollment matching itself is covered by the untouched, passing test suite |

Served-bundle check (`Invoke-WebRequest http://localhost:8000/static/app.js`, 92 110 bytes) confirms all six new markers are deployed: `monFpsText`, `x-frame-kind`, `overlayMaxLagMs`, `retryDelayMs`, `video_age_s`, `Reconnecting in`.

**Important honest finding:** in this environment the browser tab stays **hidden**, and the desktop browser's background throttling delivers only **~1.4 fps to the DOM** even though the server is delivering **16.69 parts/s** to that same client (a Python client measuring the identical endpoint simultaneously gets 16.69). This is a client-side background-tab policy, not a server limitation — and it is precisely why the status line was changed from an unconditional `live` to a measured rate: an operator now sees the real number instead of an unverified claim. **No rendered-FPS figure is claimed for this environment** (L-2); in a foreground tab the throttle does not apply.

---

## 8. Commands used for the "after" numbers

```text
.venv\Scripts\python.exe scripts\profile_publish_rate.py --camera-id 1 --duration 8 --interval 0.04
.venv\Scripts\python.exe scripts\profile_publish_rate.py --camera-id 1 --kinds --duration 12
.venv\Scripts\python.exe scripts\profile_publish_rate.py --camera-id 1 --kinds --duration 10
.venv\Scripts\python.exe scripts\profile_stream_delivery.py --camera-id 1 --start --samples 15 --window 12 --snapshots 20
.venv\Scripts\python.exe scripts\profile_stream_delivery.py --camera-id 1 --samples 15 --window 12 --snapshots 20
.venv\Scripts\python.exe scripts\profile_stream_delivery.py --camera-id 1 --start --samples 6 --window 10 --snapshots 5   # outage run
.venv\Scripts\python.exe -m pytest                                             # full suite, sequential
docker compose build api ; docker compose up -d api worker
docker logs --tail 6 ai-video-attendance-inference-1      # det_ms / emb_ms / batched_det
docker stats --no-stream --format ... worker api inference
nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu,power.draw  (6 x 1 s)
Get-CimInstance Win32_Processor ; Get-CimInstance Win32_OperatingSystem
```

The old §8 command list is superseded by this one.
