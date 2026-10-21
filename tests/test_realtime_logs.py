"""Realtime Recognition Logs: persisted audit rows -> WebSocket -> console.

Root cause these tests pin down: the log table used to be a one-shot fetch on
section mount, and the pipeline only published attendance *state changes* -
rows written by every dedup-passing sighting (and every unknown face) were
persisted but never published, so no live path could ever show them.

Covered here:
- every persisted ``RecognitionLog`` row is published as ``recognition_log``
  with the row primary key (log_id) and the frame-capture timestamp,
- ``GET /attendance/logs?since_id=`` returns only unseen rows, ascending,
  with newest_id/has_more for cursor reconciliation,
- the authenticated ``/ws/live-feed`` delivers the new event type to an
  admin and still refuses teachers with 4401,
- the static frontend wiring contract (single listener registry, single
  fallback loop, cursor polling, IST timestamps, dedupe by log_id).

Everything here is mocked/hermetic (fakeredis + TestClient); physical
cameras and the GPU inference service are NOT exercised.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _backlog(fake_redis) -> list[dict]:
    """Events published by the app, oldest first (Redis lpush order)."""
    from app.config import settings

    raw = fake_redis.lrange(settings.events_recent_key, 0, -1)
    return [json.loads(r) for r in reversed(raw)]


def _log_events(fake_redis) -> list[dict]:
    return [e for e in _backlog(fake_redis) if e.get("type") == "recognition_log"]


def _mk_student(db, name="RT Student", reg="RT001", pos=0):
    from app.crypto import encrypt_embeddings
    from app.models import Student

    vec = [0.0] * 512
    vec[pos] = 1.0
    s = Student(
        name=name,
        registration_no=reg,
        section="A",
        embeddings=encrypt_embeddings([vec]),
        photo_paths=["rt.jpg"],
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _mk_camera(db, name="RT Cam", cam_type="entry"):
    from app.models import Camera

    cam = Camera(name=name, type=cam_type, file_path="x.mp4", sampling_rate=1)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return cam


def _packet(seq=1, offset=0.0):
    from app.pipeline.frame_reader import FramePacket

    return FramePacket(
        seq=seq, ts=time.time() + offset, image=np.zeros((64, 64, 3), dtype=np.uint8)
    )


def _faces(vec):
    return [{"embedding": vec, "bbox": [0, 0, 10, 10], "score": 0.99}]


# ------------------------------------------------------- event payload shape
def test_recognition_log_event_shape_carries_row_id_and_row_time():
    from app.models import RecognitionLog
    from app.services.events import recognition_log_event
    from app.timeutil import utcnow

    class _Cam:
        id, name, type, location = 7, "Corridor", "in", "Block A"

    class _Stu:
        id, name, registration_no, section = 3, "S", "R1", "A"

    row_ts = utcnow() - timedelta(seconds=9)
    ev = recognition_log_event(
        log_id=42,
        camera=_Cam(),
        student=_Stu(),
        confidence=0.9123,
        inference_ms=12.5,
        timestamp=row_ts,
        kind="recognized",
    )
    assert ev["type"] == "recognition_log"
    assert ev["log_id"] == 42
    assert ev["kind"] == "recognized"
    # The published timestamp is the ROW time (frame capture), not "now".
    assert ev["ts"] == row_ts.isoformat()
    assert ev["student"]["id"] == 3
    assert ev["camera"]["id"] == 7
    assert ev["confidence"] == pytest.approx(0.9123)
    assert RecognitionLog.__tablename__ == "recognition_logs"


def test_recognition_log_event_unknown_face_has_null_student():
    from app.services.events import recognition_log_event
    from app.timeutil import utcnow

    class _Cam:
        id, name, type, location = 1, "Door", "in", None

    ev = recognition_log_event(
        log_id=9,
        camera=_Cam(),
        student=None,
        confidence=0.5,
        inference_ms=None,
        timestamp=utcnow(),
        kind="unknown",
    )
    assert ev["student"] is None
    assert ev["kind"] == "unknown"
    assert ev["inference_ms"] is None


# ------------------------------------------------------ pipeline publication
def test_pipeline_publishes_recognition_log_for_every_recognized_row(
    db, fake_redis, fake_embed
):
    """A persisted recognized-student row is published with its row id."""
    from app.matching import EmbeddingIndex
    from app.models import RecognitionLog, Student
    from app.pipeline.camera_task import _handle_faces

    student = _mk_student(db)
    cam = _mk_camera(db)
    index = EmbeddingIndex()
    index.refresh()

    vec = [0.0] * 512
    vec[0] = 1.0
    matched, events = _handle_faces(
        db, fake_redis, cam, _packet(), _faces(vec), index, {"total_ms": 5.0}
    )
    assert matched == 1 and events == 1

    rows = db.query(RecognitionLog).all()
    assert len(rows) == 1
    logged = _log_events(fake_redis)
    assert len(logged) == 1, "the persisted row must be published once"
    ev = logged[0]
    assert ev["log_id"] == rows[0].id
    assert ev["kind"] == "recognized"
    assert ev["student"]["id"] == student.id
    assert ev["camera"]["id"] == cam.id
    # Frame-capture time, so the console shows the real recognition moment.
    from datetime import datetime as _dt, timezone as _tz

    ev_ts = _dt.fromisoformat(ev["ts"])
    row_ts = rows[0].timestamp
    if row_ts.tzinfo is None:
        # SQLite stores UTC but drops tzinfo on the way back out.
        row_ts = row_ts.replace(tzinfo=_tz.utc)
    assert ev_ts == row_ts, "published ts must be the row's frame-capture time"
    # The pre-existing state-change event is untouched (monitor panel).
    types = [e["type"] for e in _backlog(fake_redis)]
    assert "recognition" in types


def test_pipeline_publishes_recognition_log_for_unknown_face(db, fake_redis):
    """Unknown-face rows are published too, with a null student block."""
    from app.matching import EmbeddingIndex
    from app.models import RecognitionLog
    from app.pipeline.camera_task import _handle_faces

    cam = _mk_camera(db)
    index = EmbeddingIndex()
    index.refresh()

    stranger = [0.0] * 512
    stranger[511] = 1.0  # not enrolled anywhere
    matched, events = _handle_faces(
        db, fake_redis, cam, _packet(), _faces(stranger), index, {"total_ms": 5.0}
    )
    assert matched == 0 and events == 1

    rows = db.query(RecognitionLog).all()
    assert len(rows) == 1 and rows[0].student_id is None
    logged = _log_events(fake_redis)
    assert len(logged) == 1
    assert logged[0]["log_id"] == rows[0].id
    assert logged[0]["kind"] == "unknown"
    assert logged[0]["student"] is None
    types = [e["type"] for e in _backlog(fake_redis)]
    assert "unknown_face" in types  # monitor/alert event still published


def test_dedup_suppressed_row_is_published_when_dedup_log_all(
    db, fake_redis, monkeypatch
):
    """A sighting that changes no attendance state still writes a row under
    DEDUP_LOG_ALL - and that row is published, so the console never shows a
    stale subset of the table."""
    from app.config import settings
    from app.matching import EmbeddingIndex
    from app.models import RecognitionLog
    from app.pipeline.camera_task import _handle_faces

    monkeypatch.setattr(settings, "dedup_log_all", True)
    student = _mk_student(db)
    cam = _mk_camera(db)
    index = EmbeddingIndex()
    index.refresh()

    vec = [0.0] * 512
    vec[0] = 1.0
    _handle_faces(db, fake_redis, cam, _packet(1), _faces(vec), index, {"total_ms": 5.0})
    logged_first = len(_log_events(fake_redis))
    # Same student+camera inside the dedup window: suppressed, but logged.
    _handle_faces(db, fake_redis, cam, _packet(2, 1.0), _faces(vec), index, {"total_ms": 5.0})

    rows = db.query(RecognitionLog).all()
    assert len(rows) == 2, "dedup_log_all keeps writing the audit row"
    logged = _log_events(fake_redis)
    assert len(logged) == logged_first + 1
    assert logged[-1]["log_id"] == rows[-1].id
    assert logged[-1]["kind"] == "recognized"
    assert logged[-1]["student"]["id"] == student.id


def test_published_rows_are_still_deduplicated_by_the_pipeline(db, fake_redis):
    """Publishing per row must NOT mean publishing per frame: the existing
    dedup window still yields one row (and one event) per sighting window."""
    from app.matching import EmbeddingIndex
    from app.pipeline.camera_task import _handle_faces

    _mk_student(db)
    cam = _mk_camera(db)
    index = EmbeddingIndex()
    index.refresh()

    vec = [0.0] * 512
    vec[0] = 1.0
    for i in range(5):
        _handle_faces(
            db, fake_redis, cam, _packet(i, float(i)), _faces(vec), index, {"total_ms": 5.0}
        )

    from app.models import RecognitionLog

    assert db.query(RecognitionLog).count() == 1
    assert len(_log_events(fake_redis)) == 1


# ------------------------------------------------------------- API cursor
def _seed_logs(db, n=3):
    from app.models import RecognitionLog
    from app.timeutil import utcnow

    base = utcnow() - timedelta(minutes=10)
    rows = []
    for i in range(n):
        r = RecognitionLog(
            student_id=None,
            camera_id=None,
            timestamp=base + timedelta(seconds=i),
            confidence_score=0.5,
            gpu_inference_time_ms=1.0,
        )
        db.add(r)
        rows.append(r)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def test_logs_since_id_returns_only_unseen_rows_ascending(client, auth_header, db):
    rows = _seed_logs(db, 3)
    resp = client.get(
        f"/attendance/logs?since_id={rows[0].id}&limit=100", headers=auth_header
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [i["id"] for i in body["items"]]
    assert ids == [rows[1].id, rows[2].id], "newer rows, oldest first"
    assert body["newest_id"] == rows[2].id
    assert body["has_more"] is False
    assert body["total"] == 3  # total counts matching rows, not the window


def test_logs_since_id_at_head_returns_empty_and_no_has_more(client, auth_header, db):
    rows = _seed_logs(db, 3)
    resp = client.get(f"/attendance/logs?since_id={rows[-1].id}", headers=auth_header)
    body = resp.json()
    assert body["items"] == []
    assert body["has_more"] is False, "an empty window must end reconciliation"
    assert body["newest_id"] == rows[-1].id


def test_logs_since_id_reports_has_more_when_the_window_truncates(
    client, auth_header, db
):
    rows = _seed_logs(db, 3)
    resp = client.get(f"/attendance/logs?since_id=0&limit=2", headers=auth_header)
    body = resp.json()
    assert [i["id"] for i in body["items"]] == [rows[0].id, rows[1].id]
    assert body["has_more"] is True


def test_logs_default_shape_and_order_unchanged(client, auth_header, db):
    """Regression: without since_id the endpoint behaves exactly as before
    (timestamp desc, limit/offset/total)."""
    rows = _seed_logs(db, 3)
    resp = client.get("/attendance/logs?limit=2", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert [i["id"] for i in body["items"]] == [rows[2].id, rows[1].id]
    assert body["limit"] == 2 and body["offset"] == 0 and body["total"] == 3
    assert body["newest_id"] == rows[2].id


def test_logs_cursor_requires_authorization(client, auth_header):
    """The cursor endpoint keeps the same admin-only restriction."""
    assert client.get("/attendance/logs?since_id=0").status_code in (401, 403)

    # A teacher (valid, approved, non-admin) is refused too.
    reg = client.post(
        "/auth/register", json={"username": "t.rtlog", "password": "TeachPass123"}
    )
    assert reg.status_code in (200, 201), reg.text
    uid = reg.json()["id"]
    client.post(
        f"/auth/admin/users/{uid}/approve",
        json={"security_level": 1},
        headers=auth_header,
    )
    client.patch(f"/auth/admin/users/{uid}", json={"role": "teacher"}, headers=auth_header)
    tok = client.post(
        "/auth/token", json={"username": "t.rtlog", "password": "TeachPass123"}
    ).json()["access_token"]
    resp = client.get(
        "/attendance/logs?since_id=0", headers={"Authorization": f"Bearer {tok}"}
    )
    assert resp.status_code in (401, 403)


# ------------------------------------------------------------ websocket
def test_ws_delivers_recognition_log_from_the_bus_backlog(client, auth_header, monkeypatch):
    """A row published to ``events:live`` reaches a connected admin client
    through the real /ws/live-feed endpoint (backlog replay path)."""
    import redis.asyncio as aioredis

    payload = {
        "type": "recognition_log",
        "ts": "2026-10-21T07:30:06+00:00",
        "log_id": 1234,
        "kind": "recognized",
        "camera": {"id": 1, "name": "C1", "type": "in", "location": None},
        "student": {"id": 5, "name": "S", "registration_no": "R1", "section": "A"},
        "confidence": 0.9,
        "inference_ms": 8.0,
    }
    newest_first = [json.dumps(payload)]  # lpush order; ws.py reverses it

    import asyncio

    class _PubSub:
        async def subscribe(self, *a, **k):
            pass

        async def listen(self):
            if False:  # pragma: no cover - makes this an async generator
                yield None
            await asyncio.Event().wait()  # stay subscribed; nothing to send

        async def unsubscribe(self, *a, **k):
            pass

        async def aclose(self):
            pass

    class _AsyncRedis:
        def pubsub(self):
            return _PubSub()

        async def lrange(self, *a, **k):
            return newest_first

        async def aclose(self):
            pass

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _AsyncRedis())

    token = auth_header["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "recognition_log"
        assert msg["log_id"] == 1234
        assert msg["student"]["id"] == 5


def test_ws_streams_live_recognition_log_event(client, auth_header, monkeypatch):
    """The pub/sub path forwards a freshly published row to the client."""
    import redis.asyncio as aioredis

    payload = json.dumps(
        {
            "type": "recognition_log",
            "ts": "2026-10-21T07:31:00+00:00",
            "log_id": 77,
            "kind": "unknown",
            "camera": {"id": 2, "name": "Door", "type": "in", "location": None},
            "student": None,
            "confidence": 0.42,
            "inference_ms": None,
        }
    )

    class _PubSub:
        _sent = False

        async def subscribe(self, *a, **k):
            pass

        async def listen(self):
            if not _PubSub._sent:
                _PubSub._sent = True
                yield {"type": "message", "data": payload}
            # simulate a bus hiccup so the session takes the bounded
            # resubscribe path instead of spinning
            raise ConnectionError("bus hiccup in test")

        async def unsubscribe(self, *a, **k):
            pass

        async def aclose(self):
            pass

    class _AsyncRedis:
        def pubsub(self):
            return _PubSub()

        async def lrange(self, *a, **k):
            return []

        async def aclose(self):
            pass

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _AsyncRedis())

    token = auth_header["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        seen = None
        for _ in range(12):
            msg = json.loads(ws.receive_text())
            if msg.get("type") == "recognition_log":
                seen = msg
                break
        assert seen is not None, "live pub/sub path must forward the row"
        assert seen["log_id"] == 77
        assert seen["student"] is None


def test_ws_still_rejects_teachers_with_4401(client, auth_header):
    """Role restrictions are unchanged: no student data for teachers."""
    from starlette.websockets import WebSocketDisconnect

    reg = client.post(
        "/auth/register",
        json={"username": "t.rtlog", "password": "TeachPass123"},
    )
    assert reg.status_code in (200, 201), reg.text
    uid = reg.json()["id"]
    client.post(
        f"/auth/admin/users/{uid}/approve",
        json={"security_level": 1},
        headers=auth_header,
    )
    client.patch(f"/auth/admin/users/{uid}", json={"role": "teacher"}, headers=auth_header)
    tok = client.post(
        "/auth/token", json={"username": "t.rtlog", "password": "TeachPass123"}
    ).json()["access_token"]

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/live-feed?token={tok}"):
            pass
    assert exc.value.code == 4401


# --------------------------------------------------- frontend wiring contract
def _app_js() -> str:
    return (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


def test_frontend_consumes_recognition_log_events_with_log_id_dedupe():
    js = _app_js()
    assert 'ev.type === "recognition_log"' in js or 'type === "recognition_log"' in js
    assert "log_id" in js, "dedupe key is the row primary key"
    assert "data-id" in js, "rows carry their id for ordered insertion + dedupe"


def test_frontend_uses_a_single_shared_listener_registry():
    js = _app_js()
    assert "wsSubscribe" in js and "wsSubs" in js
    assert "logLive.unsub" in js and "logLiveUnmount" in js
    # No page may assign its own onmessage: that is how duplicates appeared.
    assert "ws.onmessage =" in js  # exactly one assignment, in connectWS


def test_frontend_has_one_fallback_loop_and_no_page_reload_timer():
    js = _app_js()
    assert "logLiveSchedulePoll" in js and "logLive.pollTimer" in js
    assert "clearTimeout(logLive.pollTimer)" in js, "loop stops on unmount"
    assert "setInterval(loadAttendance" not in js, "no full-page reload timer"
    assert "since_id=" in js, "fallback poll uses the cursor, not full history"


def test_frontend_renders_log_timestamps_in_india_time():
    js = _app_js()
    assert 'timeZone: "Asia/Kolkata"' in js
    assert "fmtIN(" in js
    # REST rows arrive as naive UTC; they must be pinned to UTC before
    # parsing, or the browser reads them as local time and drifts by the
    # host offset relative to WebSocket rows.
    assert 'raw += "Z"' in js


def test_frontend_shows_connection_state_and_bounds_backoff():
    js = _app_js()
    assert "logs-status" in js and "logs-dot" in js
    assert "LOG_POLL_MAX_MS" in js, "backoff is capped"
    assert "wsAuthFailed" in js, "4401 is not retried with the same token"
    html = (REPO_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="logs-status"' in html and 'id="logs-new"' in html


def test_frontend_reseeds_dedupe_set_after_full_render():
    """The full-page render puts rows on screen via innerHTML; the dedupe set
    must be re-seeded with exactly those ids.  The WebSocket backlog replays
    the newest events on EVERY reconnect, so without re-seeding, rows already
    rendered are inserted a second time (observed live as '5 of 3' with
    duplicated ids after a socket drop + reconnect)."""
    js = _app_js()
    fetch_full = js.split("async function logLiveFetchFull()")[1].split(
        "async function logLiveFetchSince()"
    )[0]
    assert "logLive.seen.clear()" in fetch_full
    assert "logLiveRemember(id)" in fetch_full, (
        "rows rendered by the full fetch must re-enter the dedupe set"
    )


def test_frontend_mount_does_not_double_fetch_the_log_page():
    """Section entry performs ONE authoritative fetch (loadAttendance ->
    logLiveLoadFull); the mount must not arm a second immediate cycle."""
    js = _app_js()
    mount = js.split("function logLiveMount()")[1].split("function logLiveUnmount()")[0]
    assert "logLive.refetchQueued = true" not in mount
    assert "logLive.userRequested = true" not in mount
    assert "logLiveSchedulePoll(LOG_POLL_MS)" in mount
    # the explicit load still exists and is the one that fetches
    full = js.split("async function logLiveLoadFull()")[1].split(
        "function logLiveOnEvent()"
    )[0]
    assert "logLive.refetchQueued = true" in full
    assert "logLiveWake()" in full


def test_frontend_does_not_start_cameras_or_workers_from_the_log_page():
    js = _app_js()
    module = js.split("realtime recognition log")[1].split("let ws = null;")[0]
    for forbidden in ("/start", "/stop", "WebSocket(url", "new RTSP", "mjpeg"):
        assert forbidden not in module, f"log page must not touch cameras: {forbidden}"
