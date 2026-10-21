# Live Monitoring Tests (12)

## Automated (synthetic / generated clip — hardware-independent)

**13 new tests in `tests/test_live_monitor.py`, all passing:**

1. `test_live_route_declares_mjpeg_media_type` — OpenAPI documents MJPEG
2. `test_live_parts_carry_frame_data_and_camera_id` — generator yields real JPEG bytes with correct headers; exits cleanly when frames vanish
3. `test_live_parts_never_carry_credentials` — no password, host, or username in stream bytes or status JSON
4. `test_live_stream_auth_enforced` — unauthenticated → 401/403; teacher token → 403
5. `test_live_stream_serves_mjpeg_over_http` — full HTTP round-trip: status 200, correct content-type, `--frame` boundary, real JPEG payload
6. `test_live_stream_stopped_camera_reports_409` — honest reason, not an empty stream
7. `test_live_stream_running_without_frames_reports_503` — running but no frame yet → 503 with explanatory detail
8. `test_live_stream_never_carries_credentials` — API responses contain no secret
9. `test_live_status_reports_inference_outage` — `infer_ok=False` and error string present when inference is down
10. `test_viewers_share_one_capture` — two concurrent generators share the viewer counter; both release their slot on close
11. `test_build_detections_kinds` — known face → `recognized` + name; below-threshold → `unknown`; no embedding → `unverified`
12. `test_annotate_draws_scaled_boxes_with_state_colors` — green pixels for recognised, amber for unknown; bbox coordinates scaled correctly; output dimensions preserved
13. `test_label_semantics_no_fake_present` — `IN CLASS · 18/30 MIN` uses real threshold; `PRESENT` only from a stored record

**Full suite: 237 passed, 0 failed.**

## End-to-end (file-source camera, real stack)

- `scripts/validate_e2e.py`: 18/18 passed (enrollment, duplicate 409, pipeline, sessions, PRESENT summary, WebSocket events)
- File-source camera started via API; `live-status` reported `has_frame=True`, `fps=25–30`, `faces=1–2`
- `curl` fetch of `live.mjpeg` captured **15 JPEG frames in 3 seconds (~5 fps delivered)**
- Frame analysis confirmed: green box pixels (recognised identity) and amber box pixels (unknown face) both present on the same frame
- No enrolled students at test time → all faces correctly amber (not falsely identified)
- After enrolling a test identity → green box appeared alongside amber boxes

## Physical-camera acceptance

**BLOCKED** — neither camera has a stored stream password (passwords have
not been entered through the secure UI). The authenticated RTSP stream
has not been opened. `01_CAMERA_ACCEPTANCE_RESULTS.md` documents the
credential-free device interrogation that was completed.