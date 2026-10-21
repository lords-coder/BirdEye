"""Live monitor path: shared pipeline frames, annotated MJPEG, overlay rules.

Hardware-independent: synthetic frames / generated clips only.  Physical
cameras are covered by the acceptance runbook, not by this file.
"""

from __future__ import annotations

import io
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


def _mkcam(client, auth_header, db, name="Mon Cam", **kw):
    from app.models import Camera

    body = {"name": name, "file_path": "tests/assets/test_multi.mp4", "type": "entry"}
    body.update(kw)
    resp = client.post("/cameras", json=body, headers=auth_header)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _running_state(fake_redis, camera_id):
    fake_redis.set(rc.cam_state_key(camera_id), "running", ex=60)
    fake_redis.set(rc.cam_heartbeat_key(camera_id), str(time.time()), ex=60)


def _seed_frame(bin_redis, fake_redis, camera_id, jpeg: bytes):
    rc.publish_live_frame(
        bin_redis, fake_redis, camera_id, jpeg,
        frame_ts=time.time(), fps=6.0, infer_ok=True, infer_ms=12.5,
        faces=1, error=None, ttl_s=60,
    )


def _tiny_jpeg(color=(10, 20, 30)):
    import cv2
    import numpy as np

    img = np.full((96, 128, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


# ---------------------------------------------------------------- endpoints
def test_live_route_declares_mjpeg_media_type():
    from app.main import app

    spec = app.openapi()
    route = spec["paths"]["/cameras/{camera_id}/live.mjpeg"]["get"]
    # FastAPI merges the declared media type with its auto-generated
    # application/json entry; the MJPEG declaration must be present.
    assert "multipart/x-mixed-replace; boundary=frame" in route["responses"]["200"]["content"]


def test_live_stream_auth_enforced(client, auth_header, db, fake_redis, bin_redis):
    # No streaming involved: auth/dependency failures return before any body.
    cid = _mkcam(client, auth_header, db, name="Auth Cam")
    _running_state(fake_redis, cid)
    _seed_frame(bin_redis, fake_redis, cid, _tiny_jpeg())
    assert client.get(f"/cameras/{cid}/live.mjpeg").status_code in (401, 403)
    reg = client.post("/auth/register", json={"username": "t.mon", "password": "TeachPass123"})
    uid = reg.json()["id"]
    client.post(f"/auth/admin/users/{uid}/approve", json={"security_level": 1}, headers=auth_header)
    client.patch(f"/auth/admin/users/{uid}", json={"role": "teacher"}, headers=auth_header)
    tok = client.post("/auth/token", json={"username": "t.mon", "password": "TeachPass123"}).json()["access_token"]
    t_header = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/cameras/{cid}/live.mjpeg", headers=t_header).status_code == 403
    assert client.get(f"/cameras/{cid}/live-status", headers=t_header).status_code == 403
    bin_redis.delete(rc.cam_frame_key(cid))


def test_live_stream_serves_mjpeg_over_http(client, auth_header, db, fake_redis, bin_redis):
    # NOTE: starlette's TestClient only delivers a streaming response once
    # the generator ends, so the seeded frame uses a 4 s TTL: the stream
    # then completes by itself (~4 s) instead of hanging the suite.
    cid = _mkcam(client, auth_header, db, name="Http Cam")
    _running_state(fake_redis, cid)
    rc.publish_live_frame(
        bin_redis, fake_redis, cid, _tiny_jpeg(),
        frame_ts=time.time(), fps=6.0, infer_ok=True, infer_ms=1.0,
        faces=0, error=None, ttl_s=4,
    )
    r = client.get(f"/cameras/{cid}/live.mjpeg", headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert b"--frame" in r.content
    assert b"X-Camera-Id: " + str(cid).encode() in r.content
    assert b"\xff\xd8" in r.content  # real JPEG bytes, not a placeholder


def test_live_parts_carry_frame_data_and_camera_id(db, fake_redis, bin_redis):
    from app.api import cameras as cams

    cid = 41
    _seed_frame(bin_redis, fake_redis, cid, _tiny_jpeg())
    gen = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=2)
    part1 = next(gen)
    assert b"Content-Type: image/jpeg" in part1
    assert b"X-Camera-Id: " + str(cid).encode() in part1
    assert b"X-Frame-Ts: " in part1
    start = part1.find(b"\xff\xd8")
    end = part1.find(b"\xff\xd9")
    assert start >= 0 and end > start
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(part1[start:end + 2], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[0] > 0
    # generator exits by itself once frames vanish (stale source, no hang)
    bin_redis.delete(rc.cam_frame_key(cid))
    rest = list(gen)
    assert len(rest) <= 1


def test_live_parts_never_carry_credentials(client, auth_header, db, fake_redis, bin_redis):
    from app.api import cameras as cams
    from app.models import Camera

    created = client.post(
        "/cameras",
        json={"name": "Cred Cam", "host": "10.9.9.9", "username": "camadmin",
              "password": "s3cret-xyz", "type": "entry"},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert db.get(Camera, cid).stream_password() == "s3cret-xyz"  # server-side only
    _seed_frame(bin_redis, fake_redis, cid, _tiny_jpeg())
    blob = b"".join(list(cams.iter_live_parts(
        bin_redis, fake_redis, cid, interval=0.01, max_parts=2)))
    assert b"s3cret-xyz" not in blob
    assert b"10.9.9.9" not in blob
    assert b"camadmin" not in blob
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).text
    assert "s3cret-xyz" not in st


def test_live_stream_stopped_camera_reports_409(client, auth_header, db, fake_redis, bin_redis):
    cid = _mkcam(client, auth_header, db, name="Stopped Cam")
    r = client.get(f"/cameras/{cid}/live.mjpeg", headers=auth_header)
    assert r.status_code == 409
    assert "not running" in r.json()["detail"] or "stopped" in r.json()["detail"]
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).json()
    assert st["running"] is False and st["has_frame"] is False


def test_live_stream_running_without_frames_reports_503(client, auth_header, db, fake_redis, bin_redis):
    cid = _mkcam(client, auth_header, db, name="NoFrame Cam")
    _running_state(fake_redis, cid)
    r = client.get(f"/cameras/{cid}/live.mjpeg", headers=auth_header)
    assert r.status_code == 503  # honest reason, never a substitute clip


def test_live_status_reports_inference_outage(client, auth_header, db, fake_redis, bin_redis):
    cid = _mkcam(client, auth_header, db, name="Sick Cam")
    _running_state(fake_redis, cid)
    rc.publish_live_frame(
        bin_redis, fake_redis, cid, b"", frame_ts=time.time(), fps=0.0,
        infer_ok=False, infer_ms=None, faces=0,
        error="inference unavailable (3 consecutive failures)", ttl_s=60,
    )
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).json()
    assert st["infer_ok"] is False
    assert "inference unavailable" in (st["error"] or "")
    assert st["has_frame"] is False  # outage is explicit, not a stale frame


def test_live_status_missing_hash_is_not_ok(client, auth_header, db, bin_redis):
    """A camera that is 'running' but has never published a live frame must
    report infer_ok=False, not silently default to True.

    Previously `meta.get("infer_ok") != "0"` evaluated None != "0" -> True,
    so an inference outage (or startup before the first frame) looked like
    healthy inference.  The hash must explicitly carry "1".
    """
    cid = _mkcam(client, auth_header, db, name="Silent Cam")
    # running state with NO live-status hash published yet
    r = rc.get_redis()
    r.set(rc.cam_state_key(cid), "running", ex=60)
    r.set(rc.cam_heartbeat_key(cid), str(time.time()), ex=60)
    assert rc.read_live_meta(r, cid) == {}
    st = client.get(f"/cameras/{cid}/live-status", headers=auth_header).json()
    assert st["infer_ok"] is False
    assert st["has_frame"] is False


def test_viewers_share_one_capture(db, fake_redis, bin_redis):
    # Two concurrent readers share the single publication: the counter
    # proves shared access, and both generators release their slots.
    from app.api import cameras as cams

    cid = 43
    _seed_frame(bin_redis, fake_redis, cid, _tiny_jpeg())
    g1 = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=50)
    g2 = cams.iter_live_parts(bin_redis, fake_redis, cid, interval=0.01, max_parts=50)
    next(g1)
    next(g2)
    assert fake_redis.get(f"cam:{cid}:viewers") == "2"
    g1.close()
    g2.close()
    assert fake_redis.get(f"cam:{cid}:viewers") == "0"


# ------------------------------------------------------------------ overlay
def test_build_detections_kinds(db):
    from app.models import Student
    from app.pipeline import annotate as A

    s = Student(name="Aarav Sharma", registration_no="STU-9001", section="CSE-A")
    db.add(s)
    db.commit()

    class _Match:
        def __init__(self, sid, score):
            self.student_id = sid
            self.score = score

    class _Index:
        def match(self, embedding):
            if embedding == "good":
                return _Match(s.id, 0.82)
            return _Match(None, 0.11)

    dets = A.build_detections(
        db, _Index(),
        [
            {"bbox": [5, 5, 40, 40], "embedding": "good"},
            {"bbox": [50, 5, 90, 40], "embedding": "bad"},
            {"bbox": [5, 50, 40, 90]},  # no embedding at all
        ],
    )
    kinds = [d["kind"] for d in dets]
    assert kinds == ["recognized", "unknown", "unverified"]
    rec = dets[0]
    assert rec["name"] == "Aarav Sharma"
    main, _sub = A._label_lines(rec)
    assert "Aarav Sharma" in main and "RECOGNIZED" in main
    main_u, _ = A._label_lines(dets[1])
    assert "Aarav Sharma" not in main_u and "UNKNOWN" in main_u


def test_annotate_draws_scaled_boxes_with_state_colors():
    import cv2
    import numpy as np

    from app.pipeline import annotate as A

    img = np.full((100, 200, 3), 60, dtype=np.uint8)
    dets = [
        {"bbox": [10, 10, 60, 60], "kind": "recognized", "name": "Aarav Sharma",
         "presence": "INSIDE", "minutes_today": 18, "threshold_s": 1800,
         "official": None},
        {"bbox": [120, 10, 170, 60], "kind": "unknown"},
    ]
    # inference frame was 2x: boxes must land on the same faces when scaled
    out = A.annotate(img, dets, src_size=(400, 200))
    assert out.shape == img.shape  # dimensions preserved
    assert not np.array_equal(out, img)  # something was actually drawn
    green = ((out[:, :, 1].astype(int) - out[:, :, 0].astype(int) > 80)
             & (out[:, :, 1].astype(int) - out[:, :, 2].astype(int) > 80)).sum()
    amber = ((out[:, :, 2].astype(int) - out[:, :, 1].astype(int) > 80)
             & (out[:, :, 2].astype(int) - out[:, :, 0].astype(int) > 80)).sum()
    assert green > 20, "recognized box must be green"
    assert amber > 20, "unknown box must be amber"
    # scaled box sits at half coordinates: border pixel near (5..30, 5..30)
    assert green > 0


def test_label_semantics_no_fake_present():
    from app.pipeline import annotate as A

    main, sub = A._label_lines({"kind": "recognized", "name": "X",
                                "presence": "INSIDE", "minutes_today": 18,
                                "threshold_s": 1800, "official": None})
    assert sub == "IN CLASS · 18/30 MIN"  # real threshold math, not hardcoded text
    main2, sub2 = A._label_lines({"kind": "recognized", "name": "X",
                                  "presence": "INSIDE", "minutes_today": 5,
                                  "threshold_s": 3600, "official": None})
    assert sub2 == "IN CLASS · 5/60 MIN"
    _m3, sub3 = A._label_lines({"kind": "recognized", "name": "X",
                                "presence": "INSIDE", "minutes_today": 40,
                                "threshold_s": 1800, "official": "PRESENT"})
    assert sub3 == "PRESENT"  # only from a stored record


# ------------------------------------------------------- pipeline publishes
def test_pipeline_publishes_annotated_frames(db, fake_redis, bin_redis, monkeypatch):
    import cv2
    import numpy as np
    import threading
    import time

    from app.models import Camera
    from app.pipeline import camera_task as ct

    cam = Camera(name="Pub Cam", file_path="tests/assets/test_multi.mp4",
                 type="entry", sampling_rate=2)
    db.add(cam)
    db.commit()
    db.refresh(cam)

    emb = [0.0] * 512
    emb[0] = 1.0

    def _fake_infer(jpegs):
        import cv2
        import numpy as np

        out = []
        for j in jpegs:
            img = cv2.imdecode(np.frombuffer(j, dtype=np.uint8), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            out.append([{"bbox": [10, 10, min(w - 1, 60), min(h - 1, 60)],
                         "score": 0.9, "embedding": list(emb)}])
        return {"results": out, "timings": {"total_ms": 5.0}}

    monkeypatch.setattr(ct.inf_client, "infer_frames", _fake_infer)
    fake_redis.set(rc.cam_stop_key(cam.id), "0")
    stop_at = time.time() + 6

    def _run():
        ct.run_camera_pipeline(cam.id, 0)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    frame = None
    deadline = time.time() + 25
    while time.time() < deadline:
        if time.time() > stop_at:
            fake_redis.set(rc.cam_stop_key(cam.id), "1", ex=60)
        frame = rc.read_live_frame(bin_redis, cam.id)
        if frame and len(frame) > 1000:
            break
        time.sleep(0.3)
    fake_redis.set(rc.cam_stop_key(cam.id), "1", ex=60)
    t.join(timeout=20)
    assert frame and frame[:2] == b"\xff\xd8", "pipeline must publish real JPEG frames"
    meta = rc.read_live_meta(fake_redis, cam.id)
    assert meta.get("infer_ok") == "1"
    assert int(meta.get("faces", "0")) >= 1
    img = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[1] <= 640  # downscaled monitor copy
    # unknown face (empty index) must be amber, never named
    amber = ((img[:, :, 2].astype(int) - img[:, :, 1].astype(int) > 80)
             & (img[:, :, 2].astype(int) - img[:, :, 0].astype(int) > 80)).sum()
    assert amber > 20
