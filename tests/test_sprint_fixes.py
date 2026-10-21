"""Sprint 2026-10-18 regression tests: critical-bug fixes.

Covers the four production problems reported by the evaluator:
  P0-1  Camera processing must not depend on which browser page is open
  P0-2  Entry/exit camera mapping and corridor-passersby handling
  P0-3  Detection-log flood from unknown faces
  P0-4  Live-video latency (competing RTSP captures)

Hardware-independent: synthetic embeddings, fakeredis, mocked probes.
"""

from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import numpy as np
import pytest

from app.config import settings
from app.models import (
    AttendanceSession,
    Camera,
    PresenceEvent,
    PresenceState,
    RecognitionLog,
    Student,
)

UTC = timezone.utc


# ------------------------------------------------------------------ helpers
def _tiny_jpeg(color=(10, 20, 30)):
    import cv2

    img = np.full((96, 128, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _student(db, name="S", reg="STU-SP1", section="CSE-A"):
    s = Student(name=name, registration_no=reg, section=section)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _camera(db, name="Cam", cam_type="entry", role=None, **kw):
    c = Camera(name=name, type=cam_type, file_path="samples/x.mp4", **kw)
    if role is not None:
        c.camera_role = role
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _vec(dim=512, key=100):
    v = [0.0] * dim
    v[key] = 1.0
    return v


def _jitter(vec, amount=0.003):
    """Simulate realistic per-frame embedding variation."""
    out = list(vec)
    out[50] = amount
    return out


def _frame_packet(seq=1, ts=None):
    from app.pipeline.frame_reader import FramePacket

    return FramePacket(seq=seq, ts=ts or time.time(),
                       image=np.zeros((10, 10, 3), dtype=np.uint8))


@pytest.fixture()
def bin_redis(monkeypatch):
    import fakeredis

    from app import redis_client as rc

    fake = fakeredis.FakeRedis(decode_responses=False)
    monkeypatch.setattr(rc, "get_binary_redis", lambda: fake)
    return fake


def _running_state(fake_redis, camera_id):
    """Mark a camera as running (state + fresh heartbeat)."""
    from app import redis_client as rc

    fake_redis.set(rc.cam_state_key(camera_id), "running", ex=60)
    fake_redis.set(rc.cam_heartbeat_key(camera_id), str(time.time()), ex=60)


def _seed_frame(bin_redis, fake_redis, camera_id, jpeg: bytes):
    from app import redis_client as rc

    rc.publish_live_frame(
        bin_redis, fake_redis, camera_id, jpeg,
        frame_ts=time.time(), fps=6.0, infer_ok=True, infer_ms=12.5,
        faces=1, error=None, ttl_s=60,
    )


# =====================================================================
# P0-3: Detection-log flood (unknown-face dedup)
# =====================================================================
class TestUnknownFaceDedup:
    """Same unknown person across many frames must NOT flood the log.

    Before the fix, _unknown_hash() hashed raw float embeddings that
    changed every frame, so the dedup key was always new and every frame
    wrote a RecognitionLog + published an event + created an alert
    (2147 unknown rows in the production DB).
    """

    def test_same_unknown_jittered_frames_single_event(self, db, fake_redis, fake_embed):
        from app.matching import EmbeddingIndex
        from app.models import Alert
        from app.pipeline.camera_task import _handle_faces

        cam = _camera(db, name="Flood Cam")
        index = EmbeddingIndex()
        index.refresh()

        base = _vec(key=100)
        events_total = 0
        for i in range(20):
            # jitter within the same 2-dp quantisation bucket (all < 0.005)
            emb = _jitter(base, amount=0.001 + (i % 3) * 0.001)
            faces = [{"embedding": emb, "bbox": [0, 0, 10, 10], "score": 0.9}]
            pkt = _frame_packet(seq=i + 1, ts=time.time() + i)
            _m, e = _handle_faces(db, fake_redis, cam, pkt, faces, index,
                                  {"total_ms": 5.0})
            events_total += e

        assert events_total == 1, f"expected 1 event, got {events_total}"
        unknown_logs = (db.query(RecognitionLog)
                        .filter(RecognitionLog.student_id.is_(None)).all())
        assert len(unknown_logs) == 1
        alerts = db.query(Alert).filter_by(type="unknown_face").all()
        assert len(alerts) == 1

    def test_two_distinct_unknowns_get_separate_events(self, db, fake_redis, fake_embed):
        """Each distinct unknown face must still produce its own event."""
        from app.matching import EmbeddingIndex
        from app.pipeline.camera_task import _handle_faces

        cam = _camera(db, name="Multi Unknown")
        index = EmbeddingIndex()
        index.refresh()

        emb_a = _vec(key=100)
        emb_b = _vec(key=200)

        faces_a = [{"embedding": emb_a, "bbox": [0, 0, 10, 10], "score": 0.9}]
        faces_b = [{"embedding": emb_b, "bbox": [20, 20, 30, 30], "score": 0.9}]

        _m1, e1 = _handle_faces(db, fake_redis, cam, _frame_packet(1), faces_a,
                                index, {"total_ms": 5.0})
        _m2, e2 = _handle_faces(db, fake_redis, cam, _frame_packet(2), faces_b,
                                index, {"total_ms": 5.0})

        assert e1 == 1
        assert e2 == 1  # second unknown is a different person
        assert e1 + e2 == 2

    def test_unknown_hash_quantized_stable(self):
        """The hash must be stable across jittered embeddings of one face."""
        from app.pipeline.camera_task import _unknown_hash

        base = _vec(key=100)
        h1 = _unknown_hash(base)
        h2 = _unknown_hash(_jitter(base, 0.002))
        h3 = _unknown_hash(_jitter(base, 0.004))
        assert h1 == h2 == h3

    def test_unknown_hash_distinguishes_different_faces(self):
        from app.pipeline.camera_task import _unknown_hash

        ha = _unknown_hash(_vec(key=100))
        hb = _unknown_hash(_vec(key=200))
        assert ha != hb

    def test_event_volume_independent_of_frame_count(self, db, fake_redis, fake_embed):
        """Sustained detection: permanent event volume must NOT scale with
        the number of frames processed.

        Runs the same continuously-visible unknown face for 40 frames and
        asserts the resulting event/log/alert counts equal those from a
        10-frame run.  Only legitimate state changes may add events.
        """
        from app.matching import EmbeddingIndex
        from app.models import Alert
        from app.pipeline.camera_task import _handle_faces

        def run_frames(n):
            from app.db import Base, SessionLocal, engine
            from app.pipeline import camera_task as _ct
            fake_redis.flushall()  # fresh dedup keys per run
            _ct._CONFIRM_BUFFER.clear()
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine)
            db2 = SessionLocal()
            try:
                cam = _camera(db2, name=f"Vol{n}")
                index = EmbeddingIndex()
                index.refresh()
                base = _vec(key=100)
                events = 0
                for i in range(n):
                    emb = _jitter(base, amount=0.001 + (i % 4) * 0.001)
                    faces = [{"embedding": emb, "bbox": [0, 0, 10, 10], "score": 0.9}]
                    pkt = _frame_packet(seq=i + 1, ts=time.time() + i * 0.04)
                    _m, e = _handle_faces(db2, fake_redis, cam, pkt,
                                          faces, index, {"total_ms": 5.0})
                    events += e
                logs = db2.query(RecognitionLog).filter(
                    RecognitionLog.student_id.is_(None)).count()
                alerts = db2.query(Alert).filter_by(type="unknown_face").count()
                return events, logs, alerts
            finally:
                db2.rollback()
                db2.close()

        e10, l10, a10 = run_frames(10)
        e40, l40, a40 = run_frames(40)

        # 4x the frames must yield the SAME permanent volume
        assert (e10, l10, a10) == (e40, l40, a40), (
            f"event volume scaled with frame count: "
            f"10 frames={e10}/{l10}/{a10}, 40 frames={e40}/{l40}/{a40}"
        )
        # and the volume is exactly one event (one continuously-visible person)
        assert e10 == 1 and l10 == 1 and a10 == 1


# =====================================================================
# P0-1/P0-4: Snapshot must not open a competing RTSP capture
# =====================================================================
class TestSnapshotNoCompetingCapture:
    """When the pipeline is running, snapshot must read from the Redis
    frame bus instead of opening a new cv2.VideoCapture to the camera.

    Opening a second RTSP session starves the pipeline's FrameReader
    (cameras allow 1-3 concurrent sessions), causing the pipeline to drop
    frames or go offline whenever a browser preview is open.
    """

    def test_snapshot_reads_redis_when_pipeline_running(
        self, client, auth_header, db, fake_redis, bin_redis
    ):
        from app import redis_client as rc

        cam = _camera(db, name="SnapCam", host="192.0.2.1", port=554)
        _running_state(fake_redis, cam.id)
        jpeg = _tiny_jpeg()
        _seed_frame(bin_redis, fake_redis, cam.id, jpeg)

        r = client.get(f"/cameras/{cam.id}/snapshot", headers=auth_header)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8"  # JPEG magic

    def test_snapshot_direct_probe_when_pipeline_stopped(
        self, client, auth_header, db, fake_redis, monkeypatch
    ):
        from app.api import cameras as cams

        cam = _camera(db, name="StoppedCam", host="192.0.2.2", port=554)
        # pipeline not running: no state key set

        called = {"n": 0}

        def fake_probe(url):
            called["n"] += 1
            return _tiny_jpeg()

        monkeypatch.setattr(cams.camera_test, "read_frame_jpeg", fake_probe)
        r = client.get(f"/cameras/{cam.id}/snapshot", headers=auth_header)
        assert r.status_code == 200
        assert called["n"] == 1  # direct probe was used (pipeline stopped)

    def test_snapshot_never_calls_probe_when_running(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        from app.api import cameras as cams

        cam = _camera(db, name="NeverProbe", host="192.0.2.3", port=554)
        _running_state(fake_redis, cam.id)
        _seed_frame(bin_redis, fake_redis, cam.id, _tiny_jpeg())

        def fail_probe(url):
            raise AssertionError(
                "read_frame_jpeg was called while pipeline was running — "
                "competing RTSP capture!"
            )

        monkeypatch.setattr(cams.camera_test, "read_frame_jpeg", fail_probe)
        r = client.get(f"/cameras/{cam.id}/snapshot", headers=auth_header)
        assert r.status_code == 200  # served from Redis, probe never called

    def test_snapshot_running_but_no_frame_returns_503_not_probe(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        """A running pipeline with an empty frame bus must NOT fall back to
        a direct RTSP probe (that is the competing capture this fix removes).
        It must report 503 so the UI can say 'source still connecting'."""
        from app.api import cameras as cams

        cam = _camera(db, name="NoFrameYet", host="192.0.2.4", port=554)
        _running_state(fake_redis, cam.id)
        # deliberately NO frame seeded

        def fail_probe(url):
            raise AssertionError(
                "read_frame_jpeg was called while pipeline was running — "
                "competing RTSP capture!"
            )

        monkeypatch.setattr(cams.camera_test, "read_frame_jpeg", fail_probe)
        r = client.get(f"/cameras/{cam.id}/snapshot", headers=auth_header)
        assert r.status_code == 503
        assert "no frames" in r.json()["detail"]

    def test_snapshot_running_no_frame_reports_pipeline_error(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        """When the pipeline itself reports an error (e.g. RTSP auth failed)
        the snapshot 503 carries that error text, not a generic message."""
        import json as _json

        from app import redis_client as rc
        from app.api import cameras as cams

        cam = _camera(db, name="ErrCam", host="192.0.2.5", port=554)
        _running_state(fake_redis, cam.id)
        fake_redis.hset(rc.cam_live_key(cam.id), mapping={
            "ts": repr(time.time()), "fps": "0.0", "infer_ok": "0",
            "infer_ms": "", "faces": "0", "error": "401 Unauthorized",
        })

        monkeypatch.setattr(
            cams.camera_test, "read_frame_jpeg",
            lambda url: (_ for _ in ()).throw(AssertionError("probe called")),
        )
        r = client.get(f"/cameras/{cam.id}/snapshot", headers=auth_header)
        assert r.status_code == 503
        assert "401 Unauthorized" in r.json()["detail"]


    def test_legacy_stream_running_no_frame_503_not_probe(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        """Legacy stream.mjpeg must also refuse to open a probe capture
        while a pipeline is running (same defect class as snapshot)."""
        from app.api import cameras as cams

        cam = _camera(db, name="LegacyNoFrame", host="192.0.2.6", port=554)
        _running_state(fake_redis, cam.id)

        monkeypatch.setattr(
            cams.camera_test, "read_frame_jpeg",
            lambda url: (_ for _ in ()).throw(AssertionError("probe called")),
        )
        r = client.get(f"/cameras/{cam.id}/stream.mjpeg", headers=auth_header)
        assert r.status_code == 503

    def test_legacy_stream_running_serves_redis(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        """Legacy stream.mjpeg serves the shared publication when running."""
        from app.api import cameras as cams

        cam = _camera(db, name="LegacyServes", host="192.0.2.7", port=554)
        _running_state(fake_redis, cam.id)
        # 2 s TTL so the TestClient stream completes on its own
        from app import redis_client as rc

        rc.publish_live_frame(
            bin_redis, fake_redis, cam.id, _tiny_jpeg(),
            frame_ts=time.time(), fps=6.0, infer_ok=True, infer_ms=1.0,
            faces=0, error=None, ttl_s=2,
        )

        monkeypatch.setattr(
            cams.camera_test, "read_frame_jpeg",
            lambda url: (_ for _ in ()).throw(AssertionError("probe called")),
        )
        r = client.get(f"/cameras/{cam.id}/stream.mjpeg", headers=auth_header)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert b"\xff\xd8" in r.content


# =====================================================================
# P0-2: Entry/exit mapping and corridor passersby
# =====================================================================
class TestCorridorPassersby:
    """A face seen only on the corridor/exterior camera with no session
    must NOT create an inside-presence record or a PRESENT attendance.
    It must produce NEEDS_REVIEW (exit without session).
    """

    def test_corridor_only_creates_review_not_inside(self, db, fake_redis):
        from app.services import presence as ps

        s = _student(db, reg="STU-COR1")
        exterior = _camera(db, name="Corridor", cam_type="exit",
                           role="doorway_exterior")

        out = ps.observe(db, student_id=s.id, camera=exterior,
                         ts=datetime.now(UTC))
        db.commit()
        # must NOT be INSIDE; must be a review event (exit without session)
        assert out.review_event is True
        state = db.query(PresenceState).filter_by(student_id=s.id).first()
        assert state is not None
        assert state.state != "INSIDE"
        # no attendance session invented
        assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 0
        # a review event was created
        ev = db.query(PresenceEvent).filter_by(student_id=s.id).all()
        assert len(ev) == 1
        assert ev[0].review_status == "open"

    def test_interior_creates_inside(self, db, fake_redis):
        from app.services import presence as ps

        s = _student(db, reg="STU-INT1")
        interior = _camera(db, name="Interior", cam_type="entry",
                           role="classroom_interior")

        out = ps.observe(db, student_id=s.id, camera=interior,
                         ts=datetime.now(UTC))
        db.commit()
        assert out.state == "INSIDE"
        state = db.query(PresenceState).filter_by(student_id=s.id).first()
        assert state.state == "INSIDE"


# =====================================================================
# Step 6: Occupancy — corridor traffic must not inflate it
# =====================================================================
class TestOccupancy:
    """Occupancy = number of students in INSIDE state.  Corridor traffic
    must not change it; confirmed inward/outward transitions must."""

    def _inside_count(self, db):
        return db.query(PresenceState).filter_by(state="INSIDE").count()

    def test_corridor_traffic_does_not_change_occupancy(self, db, fake_redis):
        from app.services import presence as ps

        interior = _camera(db, name="Occ In", cam_type="entry",
                           role="classroom_interior")
        exterior = _camera(db, name="Occ Out", cam_type="exit",
                           role="doorway_exterior")
        # one student genuinely inside
        inside_stu = _student(db, name="In", reg="STU-OCC1")
        ps.observe(db, student_id=inside_stu.id, camera=interior,
                   ts=datetime.now(UTC))
        db.commit()
        assert self._inside_count(db) == 1

        # five different passersby seen only on the corridor camera
        for i in range(5):
            passer = _student(db, name=f"Pass{i}", reg=f"STU-PASS{i}")
            out = ps.observe(db, student_id=passer.id, camera=exterior,
                             ts=datetime.now(UTC))
            db.commit()
            # each gets a review event, never an inside state
            assert out.state != "INSIDE"

        # occupancy unchanged
        assert self._inside_count(db) == 1

    def test_inward_and_outward_transitions_update_occupancy(
        self, db, fake_redis
    ):
        from app.services import presence as ps

        interior = _camera(db, name="Occ2 In", cam_type="entry",
                           role="classroom_interior")
        exterior = _camera(db, name="Occ2 Out", cam_type="exit",
                           role="doorway_exterior")
        s = _student(db, reg="STU-OCC2")
        t0 = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)

        assert self._inside_count(db) == 0
        ps.observe(db, student_id=s.id, camera=interior, ts=t0)
        db.commit()
        assert self._inside_count(db) == 1  # inward transition

        ps.observe(db, student_id=s.id, camera=exterior,
                   ts=t0 + timedelta(minutes=20))
        db.commit()
        assert self._inside_count(db) == 0  # outward transition


# =====================================================================
# Step 7: Presence durations and period attendance
# =====================================================================
class TestDurationsAndAttendance:
    def test_separate_intervals_sum_to_total_duration(self, db, fake_redis):
        """Two separate in-class intervals (in, out, in, out) must each
        produce a session whose total_duration equals the real elapsed
        time — never wall-clock drift or processing time."""
        from app.services import presence as ps

        interior = _camera(db, name="Dur In", cam_type="entry",
                           role="classroom_interior")
        exterior = _camera(db, name="Dur Out", cam_type="exit",
                           role="doorway_exterior")
        s = _student(db, reg="STU-DUR1")

        day = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
        # interval 1: 04:00 -> 04:12 (12 min)
        ps.observe(db, student_id=s.id, camera=interior, ts=day)
        db.commit()
        ps.observe(db, student_id=s.id, camera=exterior,
                   ts=day + timedelta(minutes=12))
        db.commit()
        # interval 2: 04:20 -> 04:50 (30 min)
        ps.observe(db, student_id=s.id, camera=interior,
                   ts=day + timedelta(minutes=20))
        db.commit()
        ps.observe(db, student_id=s.id, camera=exterior,
                   ts=day + timedelta(minutes=50))
        db.commit()

        sessions = (db.query(AttendanceSession)
                    .filter_by(student_id=s.id)
                    .order_by(AttendanceSession.entry_time.asc()).all())
        assert len(sessions) == 2
        assert sessions[0].total_duration == 12 * 60
        assert sessions[1].total_duration == 30 * 60

    def test_period_attendance_uses_presence_intervals(
        self, client, auth_header, db, fake_redis
    ):
        """A student present 30 min of a 60-min period must be PRESENT when
        required_minutes=30; a 5-min presence must not be."""
        from app.services import presence as ps
        from app.models import Period, Course

        s_long = _student(db, name="Long", reg="STU-ATT1")
        s_short = _student(db, name="Short", reg="STU-ATT2")

        course = Course(code="ATT101", name="Accept", section="CSE-A")
        db.add(course)
        db.commit()
        db.refresh(course)
        period = Period(
            name="Att P", start_time=dtime(9, 0), end_time=dtime(10, 0),
            days_bitmask=127, section="CSE-A", course_id=course.id,
            required_minutes=30,
        )
        db.add(period)
        db.commit()
        db.refresh(period)

        interior = _camera(db, name="Att In", cam_type="entry",
                           role="classroom_interior")
        exterior = _camera(db, name="Att Out", cam_type="exit",
                           role="doorway_exterior")

        day = date(2026, 10, 10)  # clearly in the past => window is closed
        from app.services import attendance_engine as engine

        start, end = engine.period_window(period, day)  # aware UTC
        assert start < end

        # long student: present for the first 30 min of the window
        ps.observe(db, student_id=s_long.id, camera=interior, ts=start)
        db.commit()
        ps.observe(db, student_id=s_long.id, camera=exterior,
                   ts=start + timedelta(minutes=30))
        db.commit()
        # short student: present for only the first 5 min
        ps.observe(db, student_id=s_short.id, camera=interior, ts=start)
        db.commit()
        ps.observe(db, student_id=s_short.id, camera=exterior,
                   ts=start + timedelta(minutes=5))
        db.commit()

        resp = client.get(
            f"/attendance/period/{period.id}/records?date={day.isoformat()}",
            headers=auth_header,
        )
        assert resp.status_code == 200, resp.text
        rows = {r["student"]["registration_no"]: r for r in resp.json()["records"]}
        assert rows["STU-ATT1"]["effective_status"] == "PRESENT", rows["STU-ATT1"]
        assert rows["STU-ATT1"]["presence_seconds"] == 30 * 60
        assert rows["STU-ATT2"]["effective_status"] == "ABSENT", rows["STU-ATT2"]


# =====================================================================
# P0-1: Camera page independence (pipeline runs without frontend)
# =====================================================================class TestCameraPageIndependence:
    """Pipeline state must be tracked in Redis, not in browser memory.
    Starting camera A's pipeline must not stop camera B's pipeline.
    """

    def test_two_pipelines_independent(
        self, client, auth_header, db, fake_redis
    ):
        from app import redis_client as rc

        c1 = _create_cam_api(client, auth_header, name="Cam A",
                             host="192.0.2.10", port=554)
        c2 = _create_cam_api(client, auth_header, name="Cam B",
                             host="192.0.2.11", port=554)
        id1, id2 = c1["id"], c2["id"]

        # start A
        r1 = client.post(f"/cameras/{id1}/start", headers=auth_header)
        assert r1.status_code == 200, r1.text
        rc.touch_heartbeat(fake_redis, id1)

        # start B
        r2 = client.post(f"/cameras/{id2}/start", headers=auth_header)
        assert r2.status_code == 200, r2.text
        rc.touch_heartbeat(fake_redis, id2)

        # both running
        rt1 = rc.get_camera_runtime(fake_redis, camera_id=id1)
        rt2 = rc.get_camera_runtime(fake_redis, camera_id=id2)
        assert rt1["state"] == "starting"
        assert rt2["state"] == "starting"

        # stop A — B must still be running
        r3 = client.post(f"/cameras/{id1}/stop", headers=auth_header)
        assert r3.status_code == 200

        rt1_after = rc.get_camera_runtime(fake_redis, camera_id=id1)
        rt2_after = rc.get_camera_runtime(fake_redis, camera_id=id2)
        assert rt1_after["state"] == "stopping"
        assert rt2_after["state"] == "starting"  # B unaffected

    def test_snapshot_while_other_camera_running(
        self, client, auth_header, db, fake_redis, bin_redis, monkeypatch
    ):
        """Snapshot for camera A must not affect camera B's pipeline."""
        from app import redis_client as rc
        from app.api import cameras as cams

        c1 = _create_cam_api(client, auth_header, name="SnapA",
                             host="192.0.2.20", port=554)
        c2 = _create_cam_api(client, auth_header, name="SnapB",
                             host="192.0.2.21", port=554)

        # start B, mark it running
        client.post(f"/cameras/{c2['id']}/start", headers=auth_header)
        _running_state(fake_redis, c2["id"])
        _seed_frame(bin_redis, fake_redis, c2["id"], _tiny_jpeg())

        # mock probe so camera A's snapshot returns quickly (no 30 s timeout)
        monkeypatch.setattr(
            cams.camera_test, "read_frame_jpeg",
            lambda url: _tiny_jpeg(),
        )

        # snapshot for A (pipeline stopped) — B stays running
        r = client.get(f"/cameras/{c1['id']}/snapshot", headers=auth_header)
        # B must still be running
        rt2 = rc.get_camera_runtime(fake_redis, camera_id=c2["id"])
        assert rt2["state"] == "running"


def _create_cam_api(client, auth_header, name, **overrides):
    body = {
        "name": name,
        "type": "entry",
        "sampling_rate": 5,
        "file_path": "samples/videos/lecture.mp4",
    }
    body.update(overrides)
    resp = client.post("/cameras", json=body, headers=auth_header)
    assert resp.status_code == 201, resp.text
    return resp.json()


# =====================================================================
# Windows threads-pool worker: confirmation-buffer thread safety
# =====================================================================
class TestConfirmBufferThreadSafety:
    """On Windows the Celery prefork pool cannot start (billiard named
    semaphore PermissionError), so the worker uses a threads pool and
    several camera pipelines share camera_task._CONFIRM_BUFFER.  The
    housekeeping sweep used to race the insert/delete paths."""

    def test_concurrent_confirm_pass_does_not_raise(self, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor

        from app.pipeline import camera_task as ct
        from app.config import settings as st

        # force the sweep path (k>1 so buffers accumulate, window>0 so the
        # stale-key sweep actually iterates and deletes)
        monkeypatch.setattr(st, "confirmation_frames", 3)
        monkeypatch.setattr(st, "confirmation_window_s", 0.05)
        ct._CONFIRM_BUFFER.clear()

        def worker(seed: int) -> int:
            confirmed = 0
            for i in range(300):
                # spread across many cameras/students so the sweep is active
                cam = seed % 7
                stu = (seed + i) % 25
                # timestamps march forward so older buffers go stale
                if ct._confirm_pass(cam, stu, ts=1000.0 + i * 0.01):
                    confirmed += 1
            return confirmed

        try:
            with ThreadPoolExecutor(max_workers=8) as ex:
                results = list(ex.map(worker, range(8)))
            # no exception escaped = the lock closed the race
            assert len(results) == 8
        finally:
            ct._CONFIRM_BUFFER.clear()

    def test_confirm_pass_semantics_unchanged(self, monkeypatch):
        """The lock must not change K-of-N behaviour."""
        from app.pipeline import camera_task as ct
        from app.config import settings as st

        monkeypatch.setattr(st, "confirmation_frames", 3)
        monkeypatch.setattr(st, "confirmation_window_s", 60.0)
        ct._CONFIRM_BUFFER.clear()
        try:
            assert ct._confirm_pass(1, 1, 100.0) is False
            assert ct._confirm_pass(1, 1, 101.0) is False
            assert ct._confirm_pass(1, 1, 102.0) is True  # 3rd confirms
            # buffer released after confirmation
            assert ct._CONFIRM_BUFFER == {}
        finally:
            ct._CONFIRM_BUFFER.clear()

    def test_confirmation_disabled_short_circuits(self, monkeypatch):
        from app.pipeline import camera_task as ct
        from app.config import settings as st

        monkeypatch.setattr(st, "confirmation_frames", 1)
        assert ct._confirm_pass(9, 9, 1.0) is True
        assert ct._CONFIRM_BUFFER == {}  # never touched


# =====================================================================
# Slot admission: a dispatched start must not have its slot stolen
# =====================================================================
class TestSlotClaimIsNotStolen:
    """start_camera used to claim a slot WITHOUT writing a heartbeat, while
    _acquire_slot() reclaims any slot whose owner has no heartbeat.  Starting
    camera B inside the dispatch window (worker still starting up / busy)
    therefore DELETED camera A's slot: both pipelines then ran on
    camera_slot_0, silently defeating the CAMERA_SLOTS admission limit.
    Reproduced against the live worker on 2026-10-18 (cameras 1 and 2 both
    reported slot 0 while both were 'running').
    """

    def test_immediate_second_start_gets_a_different_slot(
        self, client, auth_header, fake_redis
    ):
        from app import redis_client as rc

        c1 = _create_cam_api(client, auth_header, name="SlotA",
                             host="192.0.2.40", port=554)
        c2 = _create_cam_api(client, auth_header, name="SlotB",
                             host="192.0.2.41", port=554)

        # NO manual touch_heartbeat() here - that is the whole point: the
        # API itself must make the claimed slot look alive.
        r1 = client.post(f"/cameras/{c1['id']}/start", headers=auth_header)
        assert r1.status_code == 200, r1.text
        r2 = client.post(f"/cameras/{c2['id']}/start", headers=auth_header)
        assert r2.status_code == 200, r2.text

        assert r1.json()["slot"] == 0
        assert r2.json()["slot"] == 1, (
            "second start stole slot 0 from the first camera: "
            f"{r2.json()}"
        )
        # the first camera still owns its slot
        assert rc.get_camera_slot(fake_redis, c1["id"]) == 0
        assert rc.get_camera_slot(fake_redis, c2["id"]) == 1
        # and its liveness was written by the API, not just by the worker
        assert rc.heartbeat_ok(fake_redis, c1["id"]) is True

    def test_third_start_hits_the_slot_limit_not_a_stolen_slot(
        self, client, auth_header, fake_redis
    ):
        """With CAMERA_SLOTS=2, two back-to-back starts must fill BOTH slots
        and the third must be rejected - not silently share one."""
        cams = [
            _create_cam_api(client, auth_header, name=f"Lim{i}",
                            host=f"192.0.2.5{i}", port=554)
            for i in range(3)
        ]
        for i in range(2):
            resp = client.post(f"/cameras/{cams[i]['id']}/start",
                               headers=auth_header)
            assert resp.status_code == 200, resp.text

        third = client.post(f"/cameras/{cams[2]['id']}/start",
                            headers=auth_header)
        assert third.status_code == 409
        assert "slots are busy" in third.json()["detail"]

    def test_enqueue_failure_clears_slot_and_heartbeat(
        self, client, auth_header, fake_redis, monkeypatch
    ):
        """If the broker rejects the dispatch, no slot/heartbeat may leak."""
        from app.api import cameras as cams

        c = _create_cam_api(client, auth_header, name="NoBroker",
                            host="192.0.2.60", port=554)

        def _boom(*a, **k):
            raise RuntimeError("broker down")

        monkeypatch.setattr(cams.celery_app, "send_task", _boom)
        resp = client.post(f"/cameras/{c['id']}/start", headers=auth_header)
        assert resp.status_code == 503

        from app import redis_client as rc

        assert rc.get_camera_slot(fake_redis, c["id"]) is None
        assert rc.heartbeat_ok(fake_redis, c["id"]) is False
        rt = rc.get_camera_runtime(fake_redis, camera_id=c["id"])
        assert rt["state"] == "stopped"


# =====================================================================
# Config sanity
# =====================================================================
class TestSprintConfig:
    def test_unknown_cooldown_config_exists(self):
        assert hasattr(settings, "unknown_cooldown_s")
        assert isinstance(settings.unknown_cooldown_s, int)

    def test_dedup_window_config_exists(self):
        assert hasattr(settings, "dedup_window_seconds")
        assert settings.dedup_window_seconds > 0
