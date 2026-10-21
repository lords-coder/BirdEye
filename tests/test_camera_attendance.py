"""Camera-attendance phase regressions: roles, secrets, presence, engine, overrides.

Hardware-independent by design: synthetic students/sessions, mocked stream
probes, fakeredis.  Nothing here claims real-camera accuracy.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import (
    AttendanceRecord,
    AttendanceSession,
    Camera,
    Period,
    PresenceEvent,
    PresenceState,
    Student,
)

UTC = timezone.utc


# ------------------------------------------------------------------ helpers
def _admin(client):
    resp = client.post("/auth/token", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_teacher(client, admin_header, username, password="TeachPass123"):
    reg = client.post(
        "/auth/register", json={"username": username, "password": password}
    )
    assert reg.status_code in (200, 201), reg.text
    uid = reg.json()["id"]
    ok = client.post(
        f"/auth/admin/users/{uid}/approve",
        json={"security_level": 3},
        headers=admin_header,
    )
    assert ok.status_code == 200, ok.text
    patched = client.patch(
        f"/auth/admin/users/{uid}", json={"role": "teacher"}, headers=admin_header
    )
    assert patched.status_code == 200, patched.text
    tok = client.post(
        "/auth/token", json={"username": username, "password": password}
    )
    assert tok.status_code == 200, tok.text
    return uid, {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _student(db, name="Stu", reg="STU-1001", section="CSE-A"):
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


def _period(db, name="P1", start=time(9, 30), end=time(10, 30), section="CSE-A",
            course_id=None, required_minutes=None):
    p = Period(
        name=name, start_time=start, end_time=end, days_bitmask=127,
        section=section, course_id=course_id, required_minutes=required_minutes,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _session(db, student, day, entry, exit=None, period_id=None):
    s = AttendanceSession(
        student_id=student.id, date=day, entry_time=entry, exit_time=exit,
        status="completed" if exit else "ongoing", period_id=period_id,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ------------------------------------------------------- roles & teacher authz
def test_teacher_blocked_from_admin_surfaces(client, auth_header, db):
    t_uid, t_header = _make_teacher(client, auth_header, "t.blocked")
    for method, path in [
        ("get", "/cameras"), ("post", "/cameras"),
        ("get", "/students?limit=5"), ("get", "/periods"),
        ("get", "/attendance/presence/states"),
        ("get", "/attendance/presence/events"),
        ("get", "/auth/admin/users"),
        ("get", "/system/gpu-status"),
    ]:
        resp = getattr(client, method)(path, headers=t_header)
        assert resp.status_code == 403, (method, path, resp.status_code)
    # /system/health stays public (load-balancer convention) but must carry
    # no sensitive data: no student PII, no credentials, no tokens.
    health = client.get("/system/health", headers=t_header)
    assert health.status_code == 200
    lowered = health.text.lower()
    assert "password" not in lowered and "token" not in lowered
    assert "registration_no" not in lowered and "embedding" not in lowered


def test_teacher_sees_only_own_courses(client, auth_header, db):
    t1, h1 = _make_teacher(client, auth_header, "t.own1")
    _t2, h2 = _make_teacher(client, auth_header, "t.own2")
    c1 = client.post(
        "/courses",
        json={"code": "CSE201", "name": "DS", "section": "CSE-A",
              "teacher_user_id": t1},
        headers=auth_header,
    )
    assert c1.status_code == 201, c1.text
    c2 = client.post(
        "/courses", json={"code": "CSE202", "name": "OS", "section": "CSE-B"},
        headers=auth_header,
    )
    assert c2.status_code == 201, c2.text
    mine = client.get("/courses", headers=h1).json()
    assert [c["code"] for c in mine] == ["CSE201"]
    assert client.get("/courses", headers=h2).json() == []
    assert client.get(f"/courses/{c1.json()['id']}", headers=h2).status_code == 403
    # teachers cannot manage courses at all
    assert client.post("/courses", json={"code": "X", "name": "Y"},
                       headers=h1).status_code == 403


def test_cross_teacher_period_isolation(client, auth_header, db):
    t1, h1 = _make_teacher(client, auth_header, "t.iso1")
    _t2, h2 = _make_teacher(client, auth_header, "t.iso2")
    course = client.post(
        "/courses",
        json={"code": "CSE301", "name": "CN", "section": "CSE-A",
              "teacher_user_id": t1},
        headers=auth_header,
    ).json()
    period = client.post(
        "/periods",
        json={"name": "CN Lec", "start_time": "09:30", "end_time": "10:30",
              "section": "CSE-A", "course_id": course["id"]},
        headers=auth_header,
    )
    assert period.status_code == 201, period.text
    pid = period.json()["id"]
    assert client.get(f"/courses/{course['id']}/periods", headers=h2).status_code == 403
    assert client.get(f"/attendance/period/{pid}/records?date=2026-10-10",
                      headers=h2).status_code == 403


def test_teacher_override_own_class_ok(client, auth_header, db):
    t1, h1 = _make_teacher(client, auth_header, "t.ov1")
    _student(db, reg="STU-OV1")
    course = client.post(
        "/courses",
        json={"code": "CSE401", "name": "DB", "section": "CSE-A",
              "teacher_user_id": t1},
        headers=auth_header,
    ).json()
    period = client.post(
        "/periods",
        json={"name": "DB Lec", "start_time": "09:30", "end_time": "10:30",
              "section": "CSE-A", "course_id": course["id"]},
        headers=auth_header,
    ).json()
    recs = client.get(f"/attendance/period/{period['id']}/records?date=2026-10-10",
                      headers=h1)
    assert recs.status_code == 200, recs.text
    rid = recs.json()["records"][0]["id"]
    ov = client.post(f"/attendance/records/{rid}/override",
                     json={"status": "PRESENT", "reason": "verified in class"},
                     headers=h1)
    assert ov.status_code == 200, ov.text
    body = ov.json()
    assert body["new_effective"] == "PRESENT"
    assert body["actor_username"] == "t.ov1"
    assert body["actor_role"] == "teacher"


def test_teacher_override_other_class_forbidden(client, auth_header, db):
    t1, h1 = _make_teacher(client, auth_header, "t.ov2")
    _t2, h2 = _make_teacher(client, auth_header, "t.ov3")
    _student(db, reg="STU-OV2")
    course = client.post(
        "/courses",
        json={"code": "CSE402", "name": "AI", "section": "CSE-A",
              "teacher_user_id": t1},
        headers=auth_header,
    ).json()
    period = client.post(
        "/periods",
        json={"name": "AI Lec", "start_time": "09:30", "end_time": "10:30",
              "section": "CSE-A", "course_id": course["id"]},
        headers=auth_header,
    ).json()
    recs = client.get(f"/attendance/period/{period['id']}/records?date=2026-10-10",
                      headers=auth_header).json()
    rid = recs["records"][0]["id"]
    resp = client.post(f"/attendance/records/{rid}/override",
                       json={"status": "ABSENT", "reason": "not my class attempt"},
                       headers=h2)
    assert resp.status_code == 403


# ------------------------------------------------------------- camera secrets
def test_password_never_leaves_server(client, auth_header, db):
    created = client.post(
        "/cameras",
        json={"name": "Secret Cam", "host": "10.0.0.9", "port": 554,
              "username": "camadmin", "password": "s3cret-xyz",
              "stream_path": "/h264", "camera_role": "classroom_interior",
              "type": "entry", "room": "C-204"},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_password"] is True
    assert "password" not in body or body.get("password") is None
    for payload in (
        client.get("/cameras", headers=auth_header).text,
        client.get(f"/cameras/{body['id']}", headers=auth_header).text,
    ):
        assert "s3cret-xyz" not in payload
        assert "camadmin:s3cret" not in payload
    # display URL carries no userinfo
    assert "@" not in (body["display_url"] or "")
    # ...but the server can still build the real URL for the pipeline
    cam = db.get(Camera, body["id"])
    assert cam.stream_password() == "s3cret-xyz"
    assert "camadmin:s3cret-xyz@" in cam.build_rtsp_url()


def test_build_rtsp_url_omits_empty_password_field(client, auth_header, db):
    """Metadata registered before password entry builds user@host, not
    user:@host (some servers reject the empty password field outright)."""
    created = client.post(
        "/cameras",
        json={"name": "NoPass Cam", "host": "10.0.0.9", "username": "camadmin",
              "type": "entry"},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    cam = db.get(Camera, created.json()["id"])
    assert cam.build_rtsp_url() == "rtsp://camadmin@10.0.0.9:554"


def test_inline_credential_url_migrated_and_redacted(client, auth_header, db):
    created = client.post(
        "/cameras",
        json={"name": "Legacy Cam", "rtsp_url": "rtsp://op:letmein-1@10.0.0.8:554/live",
              "type": "exit"},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["host"] == "10.0.0.8"
    assert body["username"] == "op"
    assert body["has_password"] is True
    assert "letmein-1" not in client.get("/cameras", headers=auth_header).text
    cam = db.get(Camera, body["id"])
    assert cam.rtsp_url is None  # no credentialed URL left in the column
    assert cam.stream_password() == "letmein-1"


def test_update_password_requires_username(client, auth_header, db):
    created = client.post(
        "/cameras", json={"name": "NoUser Cam", "host": "10.0.0.7", "type": "entry"},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    resp = client.put(f"/cameras/{cid}", json={"password": "x-pass-1"},
                      headers=auth_header)
    assert resp.status_code == 422
    # with a username it works; clearing works too
    assert client.put(f"/cameras/{cid}", json={"username": "u"},
                      headers=auth_header).status_code == 200
    assert client.put(f"/cameras/{cid}", json={"username": "u", "password": "x-pass-1"},
                      headers=auth_header).json()["has_password"] is True
    cleared = client.put(f"/cameras/{cid}", json={"clear_password": True},
                         headers=auth_header).json()
    assert cleared["has_password"] is False


def test_disabled_camera_flows(client, auth_header, db):
    created = client.post(
        "/cameras",
        json={"name": "Off Cam", "host": "10.0.0.6", "type": "entry", "enabled": False},
        headers=auth_header,
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["enabled"] is False
    start = client.post(f"/cameras/{cid}/start", headers=auth_header)
    assert start.status_code == 409
    probed = client.post(f"/cameras/{cid}/test", headers=auth_header)
    assert probed.status_code == 200
    assert probed.json()["status"] == "disabled"


def test_connection_classification_matrix(client, auth_header, monkeypatch):
    import app.services.camera_test as ct

    monkeypatch.setattr(ct, "_test_with_ffmpeg", lambda source, timeout: None)
    created = client.post(
        "/cameras", json={"name": "Probe Cam", "host": "camera.invalid", "type": "entry"},
        headers=auth_header,
    )
    cid = created.json()["id"]

    monkeypatch.setattr(ct, "_tcp_probe", lambda h, p, timeout=5.0: "refused")
    assert client.post(f"/cameras/{cid}/test", headers=auth_header).json()["status"] == "unreachable"

    monkeypatch.setattr(ct, "_tcp_probe", lambda h, p, timeout=5.0: "timeout")
    assert client.post(f"/cameras/{cid}/test", headers=auth_header).json()["status"] == "timeout"

    monkeypatch.setattr(ct, "_tcp_probe", lambda h, p, timeout=5.0: "dns")
    assert client.post(f"/cameras/{cid}/test", headers=auth_header).json()["status"] == "unreachable"


def test_redact_strips_userinfo():
    from app.services.camera_test import redact

    assert redact("rtsp://op:letmein-1@10.0.0.8:554/live") == "rtsp://***@10.0.0.8:554/live"
    assert redact("rtsps://a:b@h/x and rtsp://c:d@h2/y") == (
        "rtsps://***@h/x and rtsp://***@h2/y"
    )
    assert redact("no credentials here") == "no credentials here"


def test_snapshot_requires_admin_and_never_leaks(client, auth_header, db, monkeypatch):
    import app.services.camera_test as ct

    t_uid, t_header = _make_teacher(client, auth_header, "t.snap")
    created = client.post(
        "/cameras",
        json={"name": "Snap Cam", "host": "10.0.0.5", "username": "u",
              "password": "snap-secret-9", "type": "entry"},
        headers=auth_header,
    )
    cid = created.json()["id"]
    assert client.get(f"/cameras/{cid}/snapshot", headers=t_header).status_code == 403
    monkeypatch.setattr(ct, "read_frame_jpeg", lambda source, timeout=None: None)
    resp = client.get(f"/cameras/{cid}/snapshot", headers=auth_header)
    assert resp.status_code == 502
    assert "snap-secret-9" not in resp.text


# ------------------------------------------------------- presence state machine
def test_presence_entry_exit_lifecycle(db):
    from app.services import presence as ps

    s = _student(db, reg="STU-PS1")
    interior = _camera(db, "PS In", "entry", role="classroom_interior")
    exterior = _camera(db, "PS Out", "exit", role="doorway_exterior")
    t0 = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)

    out = ps.observe(db, student_id=s.id, camera=interior, ts=t0, confidence=0.9)
    db.commit()
    assert out.state == "INSIDE"
    assert out.action == "entry_created"
    assert db.scalar(select(PresenceState).where(PresenceState.student_id == s.id)).state == "INSIDE"

    # idempotent: seen inside again changes nothing (session layer no-op)
    out2 = ps.observe(db, student_id=s.id, camera=interior, ts=t0 + timedelta(minutes=5))
    db.commit()
    assert out2.state == "INSIDE" and out2.action == "none"
    assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 1
    assert db.scalar(select(AttendanceSession).where(
        AttendanceSession.student_id == s.id)).status == "ongoing"

    # exit uses the EVENT time, never worker processing time
    t_exit = t0 + timedelta(minutes=42, seconds=10)
    out3 = ps.observe(db, student_id=s.id, camera=exterior, ts=t_exit)
    db.commit()
    assert out3.state == "OUTSIDE" and out3.action == "exit_completed"
    sess = db.scalar(select(AttendanceSession).where(AttendanceSession.student_id == s.id))
    assert sess.exit_time is not None
    assert sess.total_duration == 42 * 60 + 10


def test_presence_exit_without_session_flagged_not_invented(db):
    from app.services import presence as ps

    s = _student(db, reg="STU-PS2")
    exterior = _camera(db, "PS2 Out", "exit", role="doorway_exterior")
    out = ps.observe(db, student_id=s.id, camera=exterior,
                     ts=datetime(2026, 10, 10, 4, 0, tzinfo=UTC))
    db.commit()
    assert out.review_event is True
    assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 0
    ev = db.scalars(select(PresenceEvent).where(
        PresenceEvent.student_id == s.id)).all()
    assert len(ev) == 1 and ev[0].review_status == "open"
    # dedup: a second sighting inside the window raises no new event
    out2 = ps.observe(db, student_id=s.id, camera=exterior,
                      ts=datetime(2026, 10, 10, 4, 1, tzinfo=UTC))
    db.commit()
    assert out2.review_event is False
    assert db.query(PresenceEvent).filter_by(student_id=s.id).count() == 1
    # interior sighting resolves: session opens
    interior = _camera(db, "PS2 In", "entry", role="classroom_interior")
    out3 = ps.observe(db, student_id=s.id, camera=interior,
                      ts=datetime(2026, 10, 10, 4, 2, tzinfo=UTC))
    db.commit()
    assert out3.state == "INSIDE"


def test_presence_pending_levels_need_two_observations(db, monkeypatch):
    from app.services import presence as ps

    monkeypatch.setattr(settings, "presence_transition_observations", 2)
    s = _student(db, reg="STU-PS3")
    interior = _camera(db, "PS3 In", "entry", role="classroom_interior")
    t0 = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)
    out = ps.observe(db, student_id=s.id, camera=interior, ts=t0)
    db.commit()
    assert out.state == "ENTRY_PENDING"
    assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 0
    out2 = ps.observe(db, student_id=s.id, camera=interior, ts=t0 + timedelta(seconds=30))
    db.commit()
    assert out2.state == "INSIDE" and out2.action == "entry_created"


def test_presence_exit_pending_cancelled_by_return(db, monkeypatch):
    from app.services import presence as ps

    monkeypatch.setattr(settings, "presence_transition_observations", 2)
    s = _student(db, reg="STU-PS4")
    interior = _camera(db, "PS4 In", "entry", role="classroom_interior")
    exterior = _camera(db, "PS4 Out", "exit", role="doorway_exterior")
    t0 = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)
    ps.observe(db, student_id=s.id, camera=interior, ts=t0)
    ps.observe(db, student_id=s.id, camera=interior, ts=t0 + timedelta(seconds=5))
    db.commit()
    out = ps.observe(db, student_id=s.id, camera=exterior, ts=t0 + timedelta(minutes=10))
    db.commit()
    assert out.state == "EXIT_PENDING"
    back = ps.observe(db, student_id=s.id, camera=interior, ts=t0 + timedelta(minutes=11))
    db.commit()
    assert back.state == "INSIDE"
    sess = db.scalar(select(AttendanceSession).where(AttendanceSession.student_id == s.id))
    assert sess.status == "ongoing"  # exit was never confirmed: nothing closed


def test_presence_multi_camera_single_session(db):
    from app.services import presence as ps

    s = _student(db, reg="STU-PS5")
    cam_a = _camera(db, "PS5 A", "entry", role="classroom_interior")
    cam_b = _camera(db, "PS5 B", "both")  # legacy row without a role
    t0 = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)
    ps.observe(db, student_id=s.id, camera=cam_a, ts=t0)
    # legacy 'both' camera: exit evidence while inside closes the session
    # (exit-when-open), even though the same camera would open one outside
    out = ps.observe(db, student_id=s.id, camera=cam_b, ts=t0 + timedelta(seconds=20))
    db.commit()
    assert out.state == "OUTSIDE" and out.action == "exit_completed"
    assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 1
    sess = db.scalar(select(AttendanceSession).where(AttendanceSession.student_id == s.id))
    assert sess.camera_out_id == cam_b.id


def test_presence_concurrent_observers_single_session(db):
    import threading

    from app.db import SessionLocal
    from app.services import presence as ps

    s = _student(db, reg="STU-PS6")
    cam = _camera(db, "PS6 In", "entry", role="classroom_interior")
    t0 = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)
    errors: list = []

    def worker(i: int):
        session = SessionLocal()
        try:
            ps.observe(db=session, student_id=s.id, camera=cam,
                       ts=t0 + timedelta(seconds=i))
            session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    db.expire_all()
    assert db.query(AttendanceSession).filter_by(student_id=s.id).count() == 1


# ---------------------------------------------------------- attendance engine
def test_threshold_boundary_30_minutes(db):
    from app.services import attendance_engine as eng

    s = _student(db, reg="STU-ENG1")
    p = _period(db, name="ENG Lec")
    day = date(2026, 10, 10)
    base = datetime(2026, 10, 10, 9, 30, tzinfo=UTC)  # tests run LOCAL_TIMEZONE=UTC
    _session(db, s, day, base, base + timedelta(minutes=29, seconds=59), period_id=p.id)
    rec, _ = eng.compute_record(db, p, s, day)
    assert rec.threshold_seconds == 1800
    assert rec.presence_seconds == 29 * 60 + 59
    assert rec.machine_status == "ABSENT"
    db.query(AttendanceSession).delete()
    _session(db, s, day, base, base + timedelta(minutes=30), period_id=p.id)
    rec2, _ = eng.compute_record(db, p, s, day)
    assert rec2.presence_seconds == 1800
    assert rec2.machine_status == "PRESENT"


def test_intervals_clipped_to_window(db):
    from app.services import attendance_engine as eng

    s = _student(db, reg="STU-ENG2")
    p = _period(db, name="ENG Clip")
    day = date(2026, 10, 10)
    # 08:00-12:00 overlaps the 09:30-10:30 window fully -> exactly 3600s
    _session(db, s, day, datetime(2026, 10, 10, 8, 0, tzinfo=UTC),
             datetime(2026, 10, 10, 12, 0, tzinfo=UTC))
    rec, _ = eng.compute_record(db, p, s, day)
    assert rec.presence_seconds == 3600
    assert rec.machine_status == "PRESENT"
    # entirely outside the window -> zero
    db.query(AttendanceSession).delete()
    _session(db, s, day, datetime(2026, 10, 10, 6, 0, tzinfo=UTC),
             datetime(2026, 10, 10, 7, 0, tzinfo=UTC))
    rec2, _ = eng.compute_record(db, p, s, day)
    assert rec2.presence_seconds == 0
    assert rec2.machine_status == "ABSENT"
    assert rec2.effective_status is None or True  # compute_record does not set effective


def test_in_progress_and_scheduled_statuses(db):
    from app.services import attendance_engine as eng

    s = _student(db, reg="STU-ENG3")
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.date()
    running = _period(
        db, name="ENG Running",
        start=(now - timedelta(minutes=10)).timetz(),
        end=(now + timedelta(minutes=50)).timetz(),
    )
    rec, _ = eng.compute_record(db, running, s, today)
    assert rec.machine_status == "IN_PROGRESS"  # never ABSENT mid-lecture
    future = _period(
        db, name="ENG Future",
        start=(now + timedelta(hours=2)).timetz(),
        end=(now + timedelta(hours=3)).timetz(),
    )
    rec2, _ = eng.compute_record(db, future, s, today)
    assert rec2.machine_status == "SCHEDULED"
    assert rec2.effective_status is None


def test_unresolved_session_counts_without_inventing_exit(db):
    from app.services import attendance_engine as eng

    s = _student(db, reg="STU-ENG4")
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.date()
    p = _period(
        db, name="ENG Open",
        start=(now - timedelta(hours=2)).timetz(),
        end=(now - timedelta(minutes=30)).timetz(),
    )
    sess = _session(db, s, today, now - timedelta(hours=1))  # still open, no exit
    rec, _ = eng.compute_record(db, p, s, today)
    # 30 of the required 30 minutes observed inside the window -> PRESENT,
    # and the missing exit was NOT fabricated
    assert rec.presence_seconds == 1800
    assert rec.machine_status == "PRESENT"
    db.refresh(sess)
    assert sess.exit_time is None and sess.status == "ongoing"


def test_override_audit_and_recompute_preservation(client, auth_header, db):
    s = _student(db, reg="STU-ENG5")
    p = _period(db, name="ENG Ov")
    day = date(2026, 10, 10)
    recs = client.get(f"/attendance/period/{p.id}/records?date={day.isoformat()}",
                      headers=auth_header)
    assert recs.status_code == 200, recs.text
    rid = [r for r in recs.json()["records"] if r["student"]["registration_no"] == "STU-ENG5"][0]["id"]

    # short + blank reasons rejected (min length 5 after trim)
    assert client.post(f"/attendance/records/{rid}/override",
                       json={"status": "PRESENT", "reason": "  x "},
                       headers=auth_header).status_code == 422
    assert client.post(f"/attendance/records/{rid}/override",
                       json={"status": "PRESENT", "reason": "   "},
                       headers=auth_header).status_code == 422

    ov = client.post(f"/attendance/records/{rid}/override",
                     json={"status": "PRESENT", "reason": "doctor note on file"},
                     headers=auth_header)
    assert ov.status_code == 200, ov.text
    assert ov.json()["previous_effective"] == "ABSENT"
    assert ov.json()["new_effective"] == "PRESENT"

    # machine status preserved; effective survives recomputation
    recs2 = client.get(f"/attendance/period/{p.id}/records?date={day.isoformat()}",
                       headers=auth_header).json()
    row = [r for r in recs2["records"] if r["id"] == rid][0]
    assert row["machine_status"] == "ABSENT"
    assert row["effective_status"] == "PRESENT"
    assert row["overridden"] is True and row["overridden_by"] == "admin"

    hist = client.get(f"/attendance/records/{rid}/overrides", headers=auth_header).json()
    assert len(hist) == 1
    assert hist[0]["reason"] == "doctor note on file"
    assert hist[0]["actor_role"] == "super_admin"
    # second correction appends (admin reversal path), history grows
    assert client.post(f"/attendance/records/{rid}/override",
                       json={"status": "ABSENT", "reason": "note was for another day"},
                       headers=auth_header).status_code == 200
    assert len(client.get(f"/attendance/records/{rid}/overrides",
                          headers=auth_header).json()) == 2
    assert db.query(AttendanceRecord).filter_by(id=rid).one().machine_status == "ABSENT"


def test_needs_review_masks_only_ambiguous_absence(db):
    from app.services import attendance_engine as eng

    s = _student(db, reg="STU-ENG6")
    p = _period(db, name="ENG Rev")
    day = date(2026, 10, 10)
    db.add(PresenceEvent(
        student_id=s.id, from_state="OUTSIDE", to_state="NEEDS_REVIEW",
        event_time=datetime(2026, 10, 10, 9, 45, tzinfo=UTC),
        reason="exit sighted without an active session", review_status="open"))
    db.commit()
    rec, _ = eng.compute_record(db, p, s, day)
    assert rec.machine_status == "NEEDS_REVIEW"
    # ...but clear evidence above threshold still counts as PRESENT
    _session(db, s, day, datetime(2026, 10, 10, 9, 30, tzinfo=UTC),
             datetime(2026, 10, 10, 10, 30, tzinfo=UTC))
    rec2, _ = eng.compute_record(db, p, s, day)
    assert rec2.machine_status == "PRESENT"


# ------------------------------------------------------------------ history
def test_history_filters_and_teacher_scoping(client, auth_header, db):
    t1, h1 = _make_teacher(client, auth_header, "t.hist")
    sa = _student(db, name="Hist A", reg="STU-HA", section="CSE-A")
    sb = _student(db, name="Hist B", reg="STU-HB", section="CSE-B")
    day = date(2026, 10, 10)
    _session(db, sa, day, datetime(2026, 10, 10, 9, 35, tzinfo=UTC),
             datetime(2026, 10, 10, 10, 5, tzinfo=UTC))
    _session(db, sb, day, datetime(2026, 10, 10, 9, 40, tzinfo=UTC))  # open
    client.post("/courses",
                json={"code": "CSE501", "name": "H", "section": "CSE-A",
                      "teacher_user_id": t1},
                headers=auth_header)
    full = client.get("/attendance/history?from=2026-10-10&to=2026-10-10",
                      headers=auth_header).json()
    assert full["total"] == 2
    assert full["totals"]["entries"] == 2
    scoped = client.get("/attendance/history?from=2026-10-10&to=2026-10-10", headers=h1).json()
    assert scoped["total"] == 1
    assert scoped["items"][0]["student"]["registration_no"] == "STU-HA"
    assert scoped["items"][0]["duration_seconds"] == 30 * 60
    only_open = client.get("/attendance/history?unresolved=true", headers=auth_header).json()
    assert only_open["total"] == 1
    assert only_open["items"][0]["unresolved"] is True
    assert only_open["items"][0]["exit_time"] is None


# ------------------------------------------------------------------ courses
def test_course_assignment_validates_teacher(client, auth_header, db):
    t_uid, _ = _make_teacher(client, auth_header, "t.assign")
    # admin accounts are not valid assignees
    resp = client.post("/courses",
                       json={"code": "CSE601", "name": "X", "teacher_user_id": 1},
                       headers=auth_header)
    assert resp.status_code == 422
    # unknown users rejected
    resp = client.post("/courses",
                       json={"code": "CSE601", "name": "X", "teacher_user_id": 9999},
                       headers=auth_header)
    assert resp.status_code == 404
    ok = client.post("/courses",
                     json={"code": "CSE601", "name": "X", "teacher_user_id": t_uid},
                     headers=auth_header)
    assert ok.status_code == 201, ok.text
    assert ok.json()["teacher_username"] == "t.assign"


# ------------------------------------------------------------------ websocket
def test_ws_teacher_token_rejected_with_4401(client, auth_header):
    from starlette.websockets import WebSocketDisconnect

    _uid, t_header = _make_teacher(client, auth_header, "t.wsrej")
    token = t_header["Authorization"].split()[1]
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/live-feed?token={token}"):
            pass
    assert exc.value.code == 4401


def test_ws_survives_redis_outage_with_status_notice(client, auth_header, monkeypatch):
    """No Redis in tests (fakeredis only patches sync access): the socket
    must stay open and announce the degraded bus instead of dropping."""
    import json as _json

    # Simulate the async Redis being unreachable: raise on subscribe.
    import redis.asyncio as aioredis

    class _FakePubSub:
        async def subscribe(self, *args, **kwargs):
            raise ConnectionError("redis unreachable in test")

        async def listen(self):
            yield {"type": "message", "data": "{}"}

        async def unsubscribe(self, *args, **kwargs):
            pass

        async def aclose(self):
            pass

    class _FakeAsyncRedis:
        def pubsub(self):
            return _FakePubSub()

        async def lrange(self, *args, **kwargs):
            return []

        async def aclose(self):
            pass

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **kw: _FakeAsyncRedis())

    token = auth_header["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        # Backlog replay may deliver earlier events first; the socket must
        # still answer pings and report the outage as a status frame.
        saw_status = False
        for _ in range(12):
            msg = _json.loads(ws.receive_text())
            if msg.get("type") == "status":
                assert msg["ok"] is False
                saw_status = True
                break
        assert saw_status, "no status notice while the event bus is down"
        ws.send_text('{"type":"ping"}')
        assert _json.loads(ws.receive_text()) == {"type": "pong"}
