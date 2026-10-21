"""Regression tests for the repair phase (see PROJECT_REPAIR_REPORT/02_BUGS_FIXED.md).

Each test targets a defect that was CONFIRMED in the audit and fixed during
the repair phase: without the fix these tests fail (TypeError, wrong status
code, leaked state, ...), with the fix they pass.

Synthetic registration numbers only - never real ones (PII scan rule).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.crypto import encrypt_embeddings
from app.models import Camera, RecognitionLog, Student

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


# --------------------------------------------------------------------- BUG-01
def test_add_photos_appends_embeddings_and_continues_file_index(db, fake_embed):
    """add_photos counted the *ciphertext string* as the gallery size.

    Pre-fix this wrote photo_<hundreds>.jpg (string length as index) and the
    max-photos check compared characters against the limit.
    """
    from app.services import enrollment

    student = enrollment.enroll_student(
        db,
        name="Synthetic Student",
        registration_no="STU900001",
        section="A",
        photos=[b"photo-a", b"photo-b", b"photo-c"],
    )
    assert len(student.embedding_list) == 3

    # an empty append is rejected up-front with a clear message
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.add_photos(db, student, [])

    updated = enrollment.add_photos(
        db, student, [b"photo-d"], filenames=["extra.jpg"]
    )
    # 3 + 1 embeddings, and the new file continues at index 3 (not at the
    # length of the encrypted string).
    from app.crypto import decrypt_embeddings

    assert len(decrypt_embeddings(updated.embeddings)) == 4
    assert len(updated.photo_paths) == 4
    assert updated.photo_paths[-1] == f"students/{student.id}/photo_3.jpg"

    # still enforced: 4 + 1 fits the default max of 5, the next one does not
    enrollment.add_photos(db, updated, [b"photo-e"])
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.add_photos(db, updated, [b"photo-f"])


# --------------------------------------------------------------------- BUG-29
def test_replace_face_rejects_duplicate_identity(db, monkeypatch):
    """replace_face skipped the duplicate-identity guard enroll_student has."""
    from app.services import enrollment

    vec_a = [1.0] + [0.0] * 511
    vec_b = [0.0, 1.0] + [0.0] * 510
    a = Student(
        name="Student A", registration_no="STU900011",
        embeddings=encrypt_embeddings([vec_a]), photo_paths=[],
    )
    b = Student(
        name="Student B", registration_no="STU900012",
        embeddings=encrypt_embeddings([vec_b]), photo_paths=[],
    )
    db.add_all([a, b])
    db.commit()

    # stubbed embedder returns B's identity -> replacing A must be refused
    monkeypatch.setattr(
        enrollment,
        "_embed_photos",
        lambda photos: [
            {"ok": True, "embedding": vec_b, "score": 0.99} for _ in photos
        ],
    )
    with pytest.raises(enrollment.EnrollmentError) as exc:
        enrollment.replace_face(db, a, [b"x1", b"x2", b"x3"])
    assert "duplicate face" in str(exc.value)

    # replacing with A's own identity is allowed (self is excluded)
    monkeypatch.setattr(
        enrollment,
        "_embed_photos",
        lambda photos: [
            {"ok": True, "embedding": vec_a, "score": 0.99} for _ in photos
        ],
    )
    out = enrollment.replace_face(db, a, [b"y1", b"y2", b"y3"])
    assert len(out.photo_paths) == 3
    assert len(out.embedding_list) == 3  # one embedding per replacement photo


# --------------------------------------------------------------------- BUG-07
def test_recognition_logs_reports_total_for_pagination(db, client, auth_header):
    """The console paginates on `total`, which the endpoint never returned."""
    for _ in range(3):
        db.add(
            RecognitionLog(
                confidence_score=0.91, gpu_inference_time_ms=12.5
            )
        )
    db.commit()

    resp = client.get("/attendance/logs?limit=2", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["limit"] == 2 and body["offset"] == 0


# --------------------------------------------------------------------- SEC-03
def test_media_requires_authentication(client):
    resp = client.get("/media/students/1/photo_0.jpg")
    assert resp.status_code == 401


def test_media_serves_files_inside_photos_root(client, auth_header):
    target_dir = settings.photos_root / "students" / "77"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "photo_0.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    resp = client.get("/media/students/77/photo_0.jpg", headers=auth_header)
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xff\xd8")


def test_media_handler_refuses_path_traversal():
    """Direct handler call: escaping DATA_DIR must 404, not serve files."""
    from fastapi import HTTPException

    from app.main import media_file

    for evil in ("../../.env", "..\\..\\settings.py", "/etc/passwd"):
        with pytest.raises(HTTPException) as exc:
            media_file(evil, _="tester")
        assert exc.value.status_code == 404, evil

    # a missing file *inside* the root is a plain 404 as well
    with pytest.raises(HTTPException) as exc:
        media_file("students/0/nope.jpg", _="tester")
    assert exc.value.status_code == 404


# --------------------------------------------------------------------- BUG-25
def test_token_with_unknown_subject_fails_closed(db, client):
    """Legacy fallback granted super admin to tokens with no DB user."""
    from app.security import create_access_token

    token, _ = create_access_token("ghost-user-xyz")
    # /students is auth-gated; pre-fix the legacy fallback resolved the
    # ghost subject to admin and returned 200.
    resp = client.get("/students", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# --------------------------------------------------------------------- BUG-24
def test_websocket_rejects_valid_signature_of_unknown_user(client):
    from starlette.websockets import WebSocketDisconnect

    from app.security import create_access_token

    token, _ = create_access_token("ghost-ws-user")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/live-feed?token={token}"):
            pass
    assert exc.value.code == 4401


# --------------------------------------------------------------------- SEC-02
def test_camera_rtsp_url_scheme_is_enforced(client, auth_header):
    for evil in (
        "javascript:alert(1)",
        '"><img src=x onerror=alert(1)>',
        "file:///etc/passwd",
        "data:text/html;base64,PHNjcmlwdD4=",
    ):
        resp = client.post(
            "/cameras",
            json={"name": "evil-cam", "rtsp_url": evil},
            headers=auth_header,
        )
        assert resp.status_code == 422, evil

    ok = client.post(
        "/cameras",
        json={"name": "good-cam", "rtsp_url": "rtsp://10.0.0.9/stream1"},
        headers=auth_header,
    )
    assert ok.status_code == 201

    upd = client.put(
        f"/cameras/{ok.json()['id']}",
        json={"rtsp_url": "file:///etc/passwd"},
        headers=auth_header,
    )
    assert upd.status_code == 422


# --------------------------------------------------------------------- BUG-23
def test_camera_read_survives_legacy_row_without_source(db, client, auth_header):
    """CameraOut inherited the create-time validator - reads could 500."""
    cam = Camera(
        name="legacy-no-source", location=None,
        rtsp_url=None, file_path=None,
        type="entry", sampling_rate=5,
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)

    one = client.get(f"/cameras/{cam.id}", headers=auth_header)
    assert one.status_code == 200
    assert one.json()["id"] == cam.id

    everything = client.get("/cameras", headers=auth_header)
    assert everything.status_code == 200


# --------------------------------------------------------------------- BUG-21
def test_confirm_buffer_is_released_and_swept(monkeypatch):
    """k-of-N buffers leaked one entry per (camera, student) forever."""
    from app.pipeline import camera_task as ct

    monkeypatch.setattr(settings, "confirmation_frames", 3)
    monkeypatch.setattr(settings, "confirmation_window_s", 5.0)

    assert ct._confirm_pass(1, 1, 1000.0) is False
    assert "1:1" in ct._CONFIRM_BUFFER

    # a student never confirmed again has its stale buffer swept on the next
    # call (pre-fix the key lingered for the worker's whole lifetime)
    assert ct._confirm_pass(2, 2, 1010.0) is False  # ts 1010 > 1000 + window
    assert "1:1" not in ct._CONFIRM_BUFFER

    # within a live window the old observation is pruned but the pair stays
    assert ct._confirm_pass(3, 3, 2000.0) is False
    assert ct._confirm_pass(3, 3, 2010.0) is False  # 2000 fell out
    assert ct._CONFIRM_BUFFER["3:3"] == [2010.0]

    # k-of-N over a fresh window still confirms and then releases the key
    assert ct._confirm_pass(4, 4, 3000.0) is False
    assert ct._confirm_pass(4, 4, 3001.0) is False
    assert ct._confirm_pass(4, 4, 3002.0) is True
    assert "4:4" not in ct._CONFIRM_BUFFER


# --------------------------------------------------------------------- BUG-11
def test_inference_batch_probe_counts_frames_not_requests():
    from app.inference.server import _frame_count

    assert _frame_count(json.dumps({"frames": ["b64-1", "b64-2", "b64-3"]})) == 3
    assert _frame_count(json.dumps({"frames": []})) == 0
    assert _frame_count(json.dumps({"kind": "embed"})) == 0
    assert _frame_count("not-json{") == 0
    assert _frame_count(b"\x00\x01garbage") == 0


# --------------------------------------------------------------------- BUG-14
def test_inference_rpc_classifies_timeout_vs_outage(monkeypatch):
    """redis TimeoutError subclasses RedisError and was misreported."""
    import redis as redis_lib

    from app.inference import client as inf

    class Boom:
        def __init__(self, exc):
            self.exc = exc

        def lpush(self, *args, **kwargs):
            raise self.exc

        def blpop(self, *args, **kwargs):
            raise self.exc

    monkeypatch.setattr(
        inf.rc, "get_rpc_redis", lambda: Boom(redis_lib.TimeoutError("slow"))
    )
    with pytest.raises(inf.InferenceTimeout):
        inf.infer_frames([b"frame-bytes"])

    monkeypatch.setattr(
        inf.rc, "get_rpc_redis", lambda: Boom(redis_lib.ConnectionError("down"))
    )
    with pytest.raises(inf.InferenceServiceDown):
        inf.infer_frames([b"frame-bytes"])


# --------------------------------------------------------------------- BUG-12
def test_celery_routes_point_only_at_existing_queues():
    from app.workers.celery_app import all_queues, celery_app

    routes = celery_app.conf.task_routes or {}
    queues = set(all_queues())
    assert "camera.run" not in routes, "dead route to camera_slot_dynamic"
    for pattern, spec in routes.items():
        queue = (spec or {}).get("queue")
        if queue is not None:
            assert queue in queues, f"{pattern} routes to missing queue {queue}"


# --------------------------------------------------------------------- BUG-13
def test_camera_run_installs_local_inference_only_in_inprocess_mode(
    monkeypatch,
):
    import app.inference.local as local_mod
    import app.pipeline.camera_task as ct
    import app.tasks as tasks

    calls = {"install": 0, "pipeline": []}

    monkeypatch.setattr(
        local_mod, "install", lambda **kw: calls.__setitem__(
            "install", calls["install"] + 1
        )
    )
    monkeypatch.setattr(
        ct, "run_camera_pipeline",
        lambda cid, slot: calls["pipeline"].append((cid, slot)) or {"ok": True},
    )

    # default (redis) mode must NOT load a worker-local model
    monkeypatch.setattr(settings, "inference_mode", "redis")
    assert tasks.camera_run.apply(args=(7, 1)).get() == {"ok": True}
    assert calls["install"] == 0

    # inprocess mode installs before the pipeline runs
    monkeypatch.setattr(settings, "inference_mode", "inprocess")
    assert tasks.camera_run.apply(args=(8, 1)).get() == {"ok": True}
    assert calls["install"] == 1
    assert calls["pipeline"] == [(7, 1), (8, 1)]


# --------------------------------------------------------------------- BUG-04
def test_alembic_upgrade_head_and_no_model_drift(tmp_path):
    """0005 aligns students.embeddings with the ORM; `alembic check` guards
    future drift (model vs migrations) automatically."""
    db_file = tmp_path / "migration_check.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}

    up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert up.returncode == 0, up.stdout + up.stderr
    combined = up.stdout + up.stderr
    assert "0005_embeddings_text" in combined, "fix migration did not run"

    chk = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert chk.returncode == 0, chk.stdout + chk.stderr


# ------------------------------------------------- quality / alerts / timeutil
def test_duplicate_face_uses_identity_threshold_not_recognition_threshold(db):
    """A different person at cosine ~0.5 must NOT count as a duplicate."""
    from app.quality import check_duplicate_face

    gallery = Student(
        name="Gallery", registration_no="STU900021",
        embeddings=encrypt_embeddings([[1.0] + [0.0] * 511]), photo_paths=[],
    )
    db.add(gallery)
    db.commit()

    different_person = [0.5, 0.8660254] + [0.0] * 510  # cosine 0.5 vs gallery
    is_dup, matched = check_duplicate_face(db, different_person)
    assert (is_dup, matched) == (False, None)

    same_person = [0.999, 0.0] + [0.0] * 510
    is_dup, matched = check_duplicate_face(db, same_person)
    assert is_dup is True and matched == gallery.id


def test_alert_dedup_window_starts_at_local_midnight(monkeypatch):
    from app.services.alerts import _local_day_start_utc

    monkeypatch.setattr(settings, "local_timezone", "Asia/Kolkata")
    start = _local_day_start_utc(date(2026, 1, 15))
    # IST is UTC+5:30 -> local midnight Jan 15 = Jan 14 18:30 UTC.
    # The pre-fix code produced Jan 15 00:00 UTC (UTC-midnight bug).
    assert start == datetime(2026, 1, 15 - 1, 18, 30, tzinfo=timezone.utc)


def test_invalid_timezone_warns_and_falls_back_to_utc(monkeypatch, caplog):
    import logging

    from app import timeutil

    monkeypatch.setattr(settings, "local_timezone", "Not/AZone")
    with caplog.at_level(logging.WARNING, logger="app.timeutil"):
        tz = timeutil.local_tz()
    assert str(tz) == "UTC"
    assert any("local_timezone" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------- BUG-03
def test_setup_student_database_main_accepts_explicit_argv(tmp_path, capsys):
    """judge_demo called main(argv) against a main() that took no args."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import inspect

    import setup_student_database as ssd

    # signature must accept an explicit argument list ...
    assert "argv" in inspect.signature(ssd.main).parameters
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("foo,bar\n1,2\n", encoding="utf-8")
    # ... and passing it must not raise TypeError (missing name/reg columns
    # come back as a clean exit code 1)
    rc = ssd.main(["--csv", str(bad_csv)])
    assert rc == 1
    out = capsys.readouterr()
    assert "FATAL" in (out.out + out.err)


# --------------------------------------------------------------------- BUG-30
def test_no_debug_print_statements_left_in_app_package():
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if re.search(r"(?<![\w.])print\s*\(", line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, "debug prints left in app/: " + ", ".join(offenders)


# --------------------------------------------------------------------- BUG-33
def _force_inference_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the inference RPC fail like an offline service (redis down)."""
    import app.inference.client as infc
    from app import quality

    def boom(jpegs):
        raise infc.InferenceServiceDown("redis unavailable: connection refused")

    monkeypatch.setattr(infc, "infer_frames", boom)
    # quality holds `client as inference_client` - same module object
    monkeypatch.setattr(quality.inference_client, "infer_frames", boom)


def test_quality_gate_surfaces_service_outage(monkeypatch) -> None:
    """An offline inference service was swallowed into 'no face detected'.

    That turned an infrastructure outage into a definitive 400 that blamed
    the user's photo (smoke_test.py expected the honest 503).
    """
    import app.inference.client as infc
    from app import quality

    _force_inference_outage(monkeypatch)
    with pytest.raises(infc.InferenceServiceDown):
        quality.check_photo_quality(b"photo-bytes")

    # generic inference errors still degrade to a normal no-face verdict
    def malformed(jpegs):
        raise infc.InferenceError("malformed inference response")

    monkeypatch.setattr(quality.inference_client, "infer_frames", malformed)
    res = quality.check_photo_quality(b"photo-bytes")
    assert res.ok is False
    assert res.reasons == ["no face detected"]


def test_enroll_answers_503_when_inference_down_during_quality_gate(
    db, client, auth_header, monkeypatch
) -> None:
    """POST /students must answer 503 (not 400) when the service is offline."""
    _force_inference_outage(monkeypatch)
    monkeypatch.setattr(
        settings, "face_quality_enabled", True
    )  # pinned false in conftest; the gate must actually run here

    files = [
        ("photos", (f"p{i}.jpg", b"\xff\xd8\xff\xe0fake" + bytes([i]), "image/jpeg"))
        for i in range(3)
    ]
    resp = client.post(
        "/students",
        data={"name": "Outage Probe", "registration_no": "STU900041"},
        files=files,
        headers=auth_header,
    )
    assert resp.status_code == 503, resp.text


def test_bulk_import_reports_outage_per_row_instead_of_crashing(
    db, tmp_path, monkeypatch
) -> None:
    """A service outage mid-import rejected the row with the real cause."""
    from app.services import enrollment

    _force_inference_outage(monkeypatch)
    monkeypatch.setattr(settings, "face_quality_enabled", True)

    data_dir = tmp_path / "photos"
    stu_dir = data_dir / "students" / "STU900042"
    stu_dir.mkdir(parents=True)
    for i in range(3):
        (stu_dir / f"photo_{i}.jpg").write_bytes(
            b"\xff\xd8\xff\xe0fake" + bytes([i])
        )
    csv_file = tmp_path / "import.csv"
    csv_file.write_text(
        "name,registration_no,section,photo_path_or_folder\n"
        "Bulk Probe,STU900042,A,students/STU900042\n",
        encoding="utf-8",
    )

    results, accepted, rejected = enrollment.bulk_enroll_from_csv(
        db, csv_file, data_dir=data_dir, skip_quality_checks=False, dry_run=True
    )
    assert accepted == 0
    assert rejected == 1
    assert "inference service unavailable" in results[0].reason


# ------------------------------------------------------------- static guards
def test_web_console_static_guards_stay_in_place():
    """Tripwires for the minimal app.js fixes (SEC-02 / BUG-05 / BUG-06)."""
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "esc(c.rtsp_url)" in js, "rtsp_url must be escaped (XSS sink)"
    assert "getToken() !== null" not in js, "reconnect guard was always true"
    assert "clearTimeout(wsRetry)" in js, "logout must stop the retry timer"
    assert "clearInterval(liveTimer)" in js, "logout must stop the poller"
