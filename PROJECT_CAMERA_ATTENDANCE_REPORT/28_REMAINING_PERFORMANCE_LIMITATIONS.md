# 28 — Remaining Performance Limitations

**Date:** 2026-10-21 · **Scope:** what is still unmeasured, unmeasurable in this environment, or deliberately left unfixed after the streaming optimization (reports 24–27). Nothing below is a defect introduced by this work; each item states its evidence, its risk, and the condition that would close it.

---

## L-1 · Physical-camera measurements — **BLOCKED**

Both Dahua cameras exist only as metadata rows: `stream_path` is `NULL`, no RTSP endpoint is configured, and camera credentials are not available to this environment. Consequently these specific figures remain **BLOCKED**:

| Requested measurement | Status | Why |
|---|---|---|
| Actual RTSP capture FPS per camera | **BLOCKED** | no stream to connect to |
| Capture-to-display latency on a real camera | **BLOCKED** | same (synthetic source decodes unpaced) |
| Behaviour under RTSP jitter / packet loss | **BLOCKED** | same |
| Rendered FPS with real camera load | **BLOCKED** | same |
| 2-camera + real-network GPU headroom | **BLOCKED** | only one synthetic camera was run |

**Everything else** requested was measured against the deployment's own mock-CCTV mechanism (a camera row pointing at a generated 2560×1440 @ 26 FPS clip) and is reported in 24/26 with the synthetic caveat attached to every number.

**To close:** configure the two cameras (stream path + credentials) and re-run the three scripts unchanged; the harnesses are source-agnostic (`--camera-id`).

---

## L-2 · Foreground rendered-FPS measurement — **BLOCKED**

The browser tab reports `visibilityState: "hidden"`, `document.hidden: true`, `hasFocus: false`, and stays hidden even after `browser.tabs.focus` (the desktop browser window is not on screen), so background throttling makes any rendered-FPS number from this environment meaningless. **No rendered-FPS figure is claimed.** The honest ceiling is the server-side delivered rate (**16.44 parts/s**, 100 % distinct, report 26 §3.1) — the browser cannot display more frames than it receives.

**To close:** run the monitor in a visible tab on the evaluation machine and read `mon.frameTimes` (the UI now displays it live as `live — 15.4 fps`).

---

## L-3 · GPU `det_ms` excursion — **resolved (no impact)**

A one-off `det_ms` rise (151–186 ms vs 105–121 baseline, `emb_ms` 20–32 vs 14–17) was observed at 14:59, immediately after the inference container finished a model re-download with a cold CUDA kernel cache, on a laptop GPU that had carried ~2 h of unrelated load. A controlled A/B (`LIVE_PUBLISH_FPS=0` → compare) was started but interrupted for an unrelated reason (changing `.env` also invalidated the inference container's config, forcing another full model re-download — see L-5 — before the tap-off window could be measured).

**Resolved by the final pass (15:45)**: the same containers, same tap setting, report `det_ms` **115.5–119.0**, `emb_ms` **15.7–16.6**, `infer_ms` mean **132.2** — statistically identical to the 133.8 / 105–121 baseline, with an identical workload (`frames:8, faces:8, batched_det:false, queue_len:0`). The tap's entire measured cost is ~19 ms of worker CPU per second (0.91 ms downscale + 0.34 ms encode × 15 fps) and it never touches the inference service, model, or RPC payload. **Conclusion: recognition was not slowed.** Recorded here (rather than deleted) because the excursion and the interrupted A/B were part of the measurement history.

---

## L-4 · Detection batching is impossible without model surgery

Startup probe (unchanged): `"detector graph has a static batch of 1 - using per-frame detection (embeddings are still batched)"`, `input_shape: [1,3,"?","?"]`, `batched_detection: false`. `ENABLE_BATCHED_DETECTION=true` therefore has **no effect** for `buffalo_l`: 8 frames cost 8 sequential SCRFD calls (`det_ms` ≈ 8 × 13 ms at baseline).

This is the **largest remaining GPU lever** and it requires re-exporting SCRFD with a dynamic batch dimension — an engine change that is explicitly out of scope and needs separate approval. Recommendation if GPU headroom must be increased for many cameras: export a batch-capable SCRFD (or a TensorRT engine) and re-measure `det_ms` with the same harness.

---

## L-5 · Model bootstrap re-downloads `buffalo_l` on every inference-container recreate — **pre-existing**

Observed twice in this session: each `up -d` that recreates the inference container re-downloads the 282 MB `buffalo_l.zip` from GitHub into **the container layer** (`/home/appuser/.insightface/models/`) rather than the mounted `insightface_models:/models` volume, costing **8–17 minutes** (`warmup_ms` alone measured 56.1 s and 30.6 s). At the observed rate (~30–95 KB/s on this link) the download dominates.

**Not fixed here**: it would alter the model bootstrap path (`scripts/download_models.py` / `INSIGHTFACE_ROOT`), which is outside the streaming scope and needs its own approval. **Recommendation:** pre-seed the `/models` volume once and make the download step conditional on file presence; that turns a 8–17 min warm-up into ~30 s.

---

## L-6 · Two environmental test failures persist

`tests/test_process_safety.py::TestSingletonMutex` (2 tests) fails only while the packaged `BirdEye.exe` (PIDs 32308/38244) holds the singleton mutex — identical to the pre-change baseline. Final suite: **340 passed, 2 failed** (baseline 322 / 2). **To close:** stop the EXE and re-run, or accept as environmental.

---

## L-7 · Container restart can strand a camera in `stopping`

Measured during this session: after `docker compose up -d api worker`, Redis held `cam:1:state = "stopping"` with the stop flag set and a **TTL of ~24 h**, while the task that owned it no longer existed — so `POST /cameras/1/start` returned **409 forever** and no process would ever clear it. Recovered by deleting only those runtime keys (`cam:1:state`, `cam:1:stop`, `cam:1:heartbeat`, `cam:1:task`, `cam:1:viewers`); no database row, no persistent data, no credential touched.

**Not fixed here** (an app-level reconcile/reaper change outside streaming scope). **Recommendation:** have the worker's start path clear a `stopping`/`starting` state whose heartbeat is stale, or let `reap_dead_cameras` reset it.

---

## L-8 · Transport ceiling and viewer scaling

MJPEG was retained (report 25 §0) because measurement showed it was not the bottleneck (5.04 parts/s delivered, `queue_len:0`, ~44 KB/s). Remaining transport facts:

* configured ceiling is now `live_mjpeg_fps = 20` (was 6, whose 0.2 s floor alone capped delivery at 5 parts/s);
* delivered rate observed: **16.44 parts/s, ~100 KB/s per viewer** at 640×q70;
* **multi-viewer bandwidth at 16 fps × N viewers was not measured beyond 2 concurrent generators** (correctness was: report 27 #19). At 100 KB/s/viewer, 10 viewers ≈ 1 MB/s per camera — fine on a LAN, unproven on Wi-Fi;
* **WebRTC / FFmpeg / GStreamer / MediaMTx were evaluated and not adopted** — with no evidence that MJPEG was the limiting factor after the fixes, replacing the transport would add a deployment dependency (a relay process, codecs, ICE/STUN) without a measured justification.

**Re-evaluation triggers (measured, not aspirational):** adopt a lower-latency transport only if, on real cameras, capture-to-display latency stays above ~500 ms after L-1 data exists, *or* per-viewer bandwidth must drop below ~50 KB/s, *or* viewer count must exceed ~20.

---

## L-9 · Preview resolution and JPEG quality were **not** retuned

Kept at `live_frame_width=640`, `live_frame_quality=70` to preserve the existing visual design and bandwidth envelope. Measured costs if raised: 1280-wide downscale + q75 encode would be roughly 2–4× the 1.33 ms display-path CPU and ~3× the 6.7 KB/frame size (~300 KB/s per viewer at 16 fps). **Not applied blind** — recognition resolution is unchanged and untouched, per the Phase-6 rule.

**To close:** encode at 1280 and measure part size + delivery FPS + worker CPU with the same scripts, then decide by number.

---

## L-10 · Worker CPU under the synthetic source is an upper bound

`docker stats` with one synthetic camera: worker **289 %**, api 9.4 %, inference 78 % (host 42 % of 28 threads). The worker figure is dominated by decoding the **unpaced** file at ~117 FPS plus full-res q85 encoding of 8 frames per batch; a 26 FPS RTSP camera performs ~5× less decode. **Not verified** on real hardware (L-1).

---

## L-11 · A disconnected MJPEG client can hold its viewer slot until the part budget ends it

Measured during cleanup: after the measurement clients had disconnected, Redis still read `cam:1:viewers = 2` (then `3` once the browser monitor genuinely connected), with the key's 60 s TTL being refreshed.

Cause: MJPEG writes into a socket whose peer has already gone often **succeed into the kernel buffer** on Windows, so the server-side generator keeps emitting into the void and keeps refreshing the key — it only notices on a failed write or when the generator is finalized. This behaviour is **pre-existing** (the baseline loop had the same structure) and is **bounded, not a leak**:

| Bound | Effect |
|---|---|
| part budget `max_parts = live_mjpeg_fps × 600` = **12 000 parts** (≈ 13 min at the observed ~15 parts/s; baseline: 3 600 parts ≈ 12 min) | generator ends by itself → `finally` releases the slot |
| `EXPIRE viewers 60` on connect/emit, never re-refreshed once emission stops | an idle key disappears within 60 s |
| **clamp at ≥ 0** on `decr` (new) | the count can never go negative, which is what previously rendered `"-1 viewer(s)"` |
| frontend reconnect | a stream that ends triggers `scheduleMonRetry(id, "stream ended")`, so the 13-minute self-termination is invisible to the operator |

**Not fixed here** (fixing it properly means write-time error detection / TCP keepalive tuning in the streaming layer, an app change outside this scope). **Recommendation:** if viewer accuracy matters operationally, derive the displayed count from `live-status` heartbeats rather than from the connection counter.

---

## L-12 · Operational, pre-existing (unchanged by this work)

* Default credentials `admin`/`admin` must be rotated after first login (`.env` `ADMIN_PASSWORD`).
* `LIVE_MJPEG_FPS` was unset in `.env` (default 6); it is still unset and now means "ceiling = 20" unless an operator overrides it.
* API `InsecureKeyLengthWarning` (HMAC key 23 bytes < 32 recommended) appears on every login — cosmetic, pre-existing.
* Seed video `samples/lecture.mp4` referenced by compose is still missing; the synthetic perf clip and the API-created camera row were left in place deliberately (documented, additive, no seed data touched).

---

## L-13 · Desktop EXE not rebuilt

The optimized code is server-side (API + worker + a static `app.js` served by the API image), so the EXE's WebView2 shell needs **no** rebuild to deliver the fix. Per release-safety rules, **no rebuild/installer was produced and none will be without explicit approval**; after approval: build once, launch once, monitor process count and RAM, stop if they multiply.

---

## Summary of what "done" looks like

| Item | State |
|---|---|
| Bottleneck identified with measurements | ✅ report 24 |
| Smallest effective fixes implemented | ✅ report 25 (8 files, no redesign) |
| Before/after numbers | ✅ report 26 (4.06× publish, 3.26× delivery, 0 duplicates, outage-proof video) |
| Regression tests | ✅ report 27 (18 new, 340 passed, 0 regressions) |
| Physical-camera numbers | ⛔ BLOCKED (L-1) |
| GPU `det_ms` attribution | ✅ resolved — excursion was ambient; recognition unchanged (L-3) |
| Model bootstrap / stale-state / transport ceiling | ⚠ documented, out of scope (L-4, L-5, L-7, L-8) |
