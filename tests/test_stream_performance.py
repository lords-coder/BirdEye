"""Streaming-performance regressions: display decoupling, change-driven MJPEG,
bounded viewer accounting, and pipeline/viewer independence.

Hardware-independent (fakeredis + generated/synthetic frames only).  These
guard the optimizations measured in PROJECT_CAMERA_ATTENDANCE_REPORT/24-28:

  * the capture thread can publish display frames INDEPENDENTLY of the
    recognition cadence (sampling_rate) and of inference latency,
  * a failing display tap can never break capture,
  * MJPEG emits changed frames (plus a bounded keep-alive) instead of
    padding the stream with duplicate JPEGs,
  * the viewer counter can never drift negative,
  * opening a stream/snapshot while a pipeline runs opens NO camera source.
"""

from __future__ import annotations

import queue as queue_mod
import threading
import time

import pytest

from app import redis_client as rc


@pytest.fixture()
def bin_redis(monkeypatch):
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=False)
    monkeypatch.setattr(rc, "get_binary_redis", lambda: fake)
    return fake


def _tiny_jpeg(color=(10, 20, 30)):
    import cv2
    import numpy as np

    img = np.full((96, 128, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _mkcam(client, auth_header, db, name="Perf Cam", **kw):
    body = {"name": name, "file_path": "tests/assets/test_multi.mp4", "type": "entry"}
    body.update(kw)
    resp = client.post("/cameras", json=body, headers=auth_header)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _running_state(fake_redis, camera_id):
    fake_redis.set(rc.cam_state_key(camera_id), "running", ex=60)
    fake_redis.set(rc.cam_heartbeat_key(camera_id), str(time.time()), ex=60)


# --------------------------------------------------------- frame blob layout
def test_frame_blob_roundtrip_marks_kind_and_timestamp(bin_redis):
    import numpy as np  # noqa: F401  (documents that frames are real bytes)

    jpeg = _tiny_jpeg()
    ts = 1700000000.125

    raw = rc.encode_live_frame(jpeg, frame_ts=ts, annotated=False)
    got_jpeg, got_ts, annotated = rc.decode_live_frame(raw)
    assert got_jpeg == jpeg and got_ts == ts and annotated is False

    ann = rc.encode_live_frame(jpeg, frame_ts=ts, annotated=True)
    got_jpeg, got_ts, annotated = rc.decode_live_frame(ann)
    assert got_jpeg == jpeg and got_ts == ts and annotated is True


def test_legacy_prefixless_blob_still_decodes_as_jpeg(bin_redis):
    """Values written by an older build must keep working (rolling deploy)."""
    jpeg = _tiny_jpeg((7, 7, 7))
    got_jpeg, got_ts, annotated = rc.decode_live_frame(jpeg)
    assert got_jpeg == jpeg and got_ts is None and annotated is False


def test_publish_live_frame_raw_leaves_inference_status_untouched(
    fake_redis, bin_redis
):
    """The display tap must not clobber the pipeline's inference status."""
    rc.publish_live_frame(
        bin_redis, fake_redis, 51, _tiny_jpeg(),
        frame_ts=time.time(), fps=6.0, infer_ok=True, infer_ms=42.0,
        faces=2, error=None, ttl_s=60,
    )
    before = rc.read_live_meta(fake_redis, 51)

    rc.publish_live_frame_raw(
        bin_redis, 51, _tiny_jpeg((80, 80, 80)), frame_ts=before["ts"], ttl_s=60
    )

    after = rc.read_live_meta(fake_redis, 51)
    assert after == before, "raw display publish must not touch the status hash"
    frame = rc.read_live_frame_ex(bin_redis, 51)
    assert frame is not None
    _jpeg, _ts, annotated = frame
    assert annotated is False
    # the snapshot path still gets plain JPEG bytes
    assert rc.read_live_frame(bin_redis, 51).startswith(b"\xff\xd8")


def test_annotated_publish_is_marked_and_carries_its_own_ts(fake_redis, bin_redis):
    ts = time.time()
    rc.publish_live_frame(
        bin_redis, fake_redis, 52, _tiny_jpeg(), frame_ts=ts, fps=6.0,
        infer_ok=True, infer_ms=12.0, faces=1, error=None, ttl_s=60,
    )
    jpeg, got_ts, annotated = rc.read_live_frame_ex(bin_redis, 52)
    assert annotated is True
    assert got_ts == pytest.approx(ts, rel=1e-6)
    assert jpeg.startswith(b"\xff\xd8")


# ----------------------------------------------------------- MJPEG delivery
def test_mjpeg_emits_changed_frames_and_bounded_keepalive(
    fake_redis, bin_redis, monkeypatch
):
    from app.api import cameras as cams
    from app.config import settings

    monkeypatch.setattr(settings, "live_mjpeg_keepalive_s", 0.05)
    cid = 61
    ts1 = time.time()
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg((1, 1, 1)), frame_ts=ts1, ttl_s=60)

    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=6)
    part1 = next(gen)
    assert b"X-Frame-Kind: raw" in part1
    assert b"X-Frame-Ts: " + repr(ts1).encode() in part1

    # Same frame re-published: no duplicate part until the keep-alive is due.
    started = time.monotonic()
    part2 = next(gen)
    waited = time.monotonic() - started
    assert waited >= 0.04, "an unchanged frame must not be re-sent immediately"
    assert b"X-Frame-Ts: " + repr(ts1).encode() in part2  # keep-alive re-send

    # A genuinely new frame is delivered promptly (change-driven, no padding).
    ts2 = ts1 + 2.0
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg((60, 60, 60)), frame_ts=ts2, ttl_s=60)
    started = time.monotonic()
    part3 = next(gen)
    assert time.monotonic() - started < 0.25, "a new frame must not wait a keep-alive"
    assert b"X-Frame-Ts: " + repr(ts2).encode() in part3
    assert b"X-Frame-Kind: raw" in part3

    # ...and the generator still ends by itself when frames vanish.
    bin_redis.delete(rc.cam_frame_key(cid))
    assert list(gen) == []


def test_mjpeg_annotated_frames_carry_annotated_kind(fake_redis, bin_redis):
    from app.api import cameras as cams

    cid = 62
    rc.publish_live_frame(
        bin_redis, fake_redis, cid, _tiny_jpeg(), frame_ts=time.time(), fps=6.0,
        infer_ok=True, infer_ms=9.0, faces=1, error=None, ttl_s=60,
    )
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=1)
    assert b"X-Frame-Kind: annotated" in next(gen)


def test_mjpeg_part_body_is_a_plain_jpeg(fake_redis, bin_redis):
    """Browsers decode the part body directly: no framing bytes allowed."""
    import cv2
    import numpy as np

    from app.api import cameras as cams

    cid = 63
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg(), frame_ts=time.time(), ttl_s=60)
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=1)
    part = next(gen)
    start = part.find(b"\xff\xd8")
    end = part.find(b"\xff\xd9")
    assert start >= 0 and end > start
    img = cv2.imdecode(
        np.frombuffer(part[start:end + 2], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert img is not None and img.shape[0] > 0


def test_annotated_frames_are_never_rate_limited_out(fake_redis, bin_redis):
    """A recognition frame must reach the browser even inside the emit gap.

    Without this, the capture tap (which publishes faster than inference)
    would make the recognition overlay rare or invisible.
    """
    from app.api import cameras as cams

    cid = 65
    ts1 = time.time()
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg((1, 1, 1)), frame_ts=ts1, ttl_s=60)
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.2, max_parts=3)
    next(gen)  # plain frame emitted at t0

    ts2 = ts1 + 1.0
    rc.publish_live_frame(
        bin_redis, fake_redis, cid, _tiny_jpeg((40, 40, 40)), frame_ts=ts2,
        fps=8.0, infer_ok=True, infer_ms=9.0, faces=1, error=None, ttl_s=60,
    )
    started = time.monotonic()
    part = next(gen)
    assert time.monotonic() - started < 0.15, "annotated frame must not wait the rate limit"
    assert b"X-Frame-Kind: annotated" in part
    assert b"X-Frame-Ts: " + repr(ts2).encode() in part


def test_raw_frames_respect_the_emit_rate_limit(fake_redis, bin_redis):
    """Plain capture frames still honour the advertised maximum FPS."""
    from app.api import cameras as cams

    cid = 66
    ts1 = time.time()
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg((2, 2, 2)), frame_ts=ts1, ttl_s=60)
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.2, max_parts=3)
    next(gen)

    rc.publish_live_frame_raw(
        bin_redis, cid, _tiny_jpeg((50, 50, 50)), frame_ts=ts1 + 1.0, ttl_s=60
    )
    started = time.monotonic()
    next(gen)
    assert time.monotonic() - started >= 0.15, "raw frames must be rate limited"


def test_viewer_counter_never_goes_negative(fake_redis, bin_redis):
    """A viewer finalised after its key expired must not create -1.

    decr on a missing key CREATES it at -1, which is exactly how the
    dashboard ended up showing "-2 viewer(s)".
    """
    from app.api import cameras as cams

    cid = 64
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg(), frame_ts=time.time(), ttl_s=60)
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=3)
    next(gen)
    assert int(fake_redis.get(f"cam:{cid}:viewers")) == 1
    fake_redis.delete(f"cam:{cid}:viewers")  # simulate the 60 s TTL expiring
    gen.close()
    assert int(fake_redis.get(f"cam:{cid}:viewers") or 0) == 0


# -------------------------------------------------------------- capture tap
def _run_reader(*, sampling_rate, display_interval_s, tap_mode="count", seconds=0.5):
    from app.pipeline.frame_reader import FrameReader

    out_q: queue_mod.Queue = queue_mod.Queue(maxsize=64)
    stop = threading.Event()
    calls = {"n": 0}

    def _tap(frame, ts):
        calls["n"] += 1
        if tap_mode == "raise":
            raise RuntimeError("display tap exploded")

    reader = FrameReader(
        camera_id=99,
        source="tests/assets/test_multi.mp4",
        sampling_rate=sampling_rate,
        out_q=out_q,
        stop_event=stop,
        on_display=_tap if tap_mode != "none" else None,
        display_interval_s=display_interval_s,
    )
    reader.start()
    time.sleep(seconds)
    stop.set()
    reader.join(timeout=5)
    assert not reader.is_alive(), "reader must stop cleanly"
    return reader, calls, out_q


def test_display_tap_is_independent_of_recognition_cadence():
    """sampling_rate throttles the GPU path only - never the displayed video."""
    reader, calls, _q = _run_reader(
        sampling_rate=60, display_interval_s=0.0, tap_mode="count"
    )
    assert reader.frames_read > 10
    assert reader.frames_sampled > 0
    # every decoded frame is available to the display...
    assert reader.display_frames == reader.frames_read == calls["n"]
    # ...while recognition sees only every 60th frame
    assert reader.display_frames > reader.frames_sampled * 5


def test_display_tap_is_rate_limited():
    reader, calls, _q = _run_reader(
        sampling_rate=1, display_interval_s=10.0, tap_mode="count", seconds=0.6
    )
    assert reader.frames_read > 10
    assert reader.display_frames <= 1, "display_interval_s must bound the tap"
    assert calls["n"] <= 1


def test_display_tap_failure_never_breaks_capture():
    """A display bug must degrade the picture, never the pipeline."""
    reader, _calls, out_q = _run_reader(
        sampling_rate=1, display_interval_s=0.0, tap_mode="raise", seconds=0.5
    )
    assert reader.display_frames == 0
    assert reader.frames_read > 10
    assert reader.frames_sampled > 10
    assert out_q.qsize() > 0, "frames must still reach the inference queue"


def test_display_tap_disabled_keeps_legacy_behaviour():
    reader, calls, _q = _run_reader(
        sampling_rate=5, display_interval_s=0.0, tap_mode="none", seconds=0.4
    )
    assert calls["n"] == 0
    assert reader.display_frames == 0
    assert reader.frames_sampled > 0


# ---------------------------------------------------------------- live-status
def test_live_status_exposes_video_age_and_kind(
    client, auth_header, db, fake_redis, bin_redis
):
    cid = _mkcam(client, auth_header, db, name="Status Kind Cam")
    _running_state(fake_redis, cid)

    # capture tap published first: the picture is raw, inference has not run
    rc.publish_live_frame_raw(
        bin_redis, cid, _tiny_jpeg(), frame_ts=time.time() - 0.4, ttl_s=60
    )
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).json()
    assert st["video_kind"] == "raw"
    assert st["video_age_s"] is not None and 0.0 <= st["video_age_s"] < 5
    assert st["infer_ok"] is False  # honest: nothing recognised yet

    # inference catches up: the same slot now holds the annotated frame
    rc.publish_live_frame(
        bin_redis, fake_redis, cid, _tiny_jpeg((20, 20, 20)),
        frame_ts=time.time(), fps=8.0, infer_ok=True, infer_ms=15.0,
        faces=1, error=None, ttl_s=60,
    )
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).json()
    assert st["video_kind"] == "annotated"
    assert st["infer_ok"] is True and st["frame_age_s"] is not None


def test_snapshot_still_serves_the_shared_frame(
    client, auth_header, db, fake_redis, bin_redis
):
    """A snapshot during a running pipeline is a bus hit, not a source probe."""
    cid = _mkcam(client, auth_header, db, name="Snap Tap Cam")
    _running_state(fake_redis, cid)
    rc.publish_live_frame_raw(
        bin_redis, cid, _tiny_jpeg((33, 44, 55)), frame_ts=time.time(), ttl_s=60
    )
    r = client.get(f"/cameras/{cid}/snapshot", headers=auth_header)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content.startswith(b"\xff\xd8")  # framing bytes are stripped


def test_running_pipeline_never_opens_the_source_for_viewers(
    client, auth_header, db, fake_redis, bin_redis, monkeypatch
):
    """No competing capture: streams/snapshots must not touch cv2.VideoCapture.

    This is the Phase-2 guarantee that N viewers never create N captures -
    enforced by making any source open explode.
    """
    import cv2

    from app.api import cameras as cams

    def _boom(*_a, **_kw):
        raise AssertionError("viewer path opened the camera source")

    monkeypatch.setattr(cv2, "VideoCapture", _boom)

    cid = _mkcam(client, auth_header, db, name="No Probe Cam")
    _running_state(fake_redis, cid)
    ts = time.time()
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg(), frame_ts=ts, ttl_s=60)

    snap = client.get(f"/cameras/{cid}/snapshot", headers=auth_header)
    assert snap.status_code == 200 and snap.content.startswith(b"\xff\xd8")

    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=1)
    assert b"Content-Type: image/jpeg" in next(gen)


def test_pipeline_and_viewers_run_concurrently_on_one_publication(
    client, auth_header, db, fake_redis, bin_redis
):
    """Two viewers + a live pipeline: one publication, no extra captures."""
    from app.api import cameras as cams

    cid = _mkcam(client, auth_header, db, name="Shared Cam")
    _running_state(fake_redis, cid)
    rc.publish_live_frame_raw(bin_redis, cid, _tiny_jpeg(), frame_ts=time.time(), ttl_s=60)

    g1 = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=2)
    g2 = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=2)
    assert b"Content-Type: image/jpeg" in next(g1)
    assert b"Content-Type: image/jpeg" in next(g2)
    assert int(fake_redis.get(f"cam:{cid}:viewers")) == 2
    g1.close()
    g2.close()
    assert int(fake_redis.get(f"cam:{cid}:viewers") or 0) == 0
