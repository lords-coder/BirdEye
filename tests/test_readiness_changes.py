"""Tests for readiness-phase decisions and fixes (PROJECT_READINESS_REPORT/).

Each test encodes a decision explicitly approved by the project owner or a
defect found during the readiness validation pass:

* D-1: confirmation rule is 3 frames / 10 s (CONTRIBUTING acceptance criteria)
* D-2: the photo quality gate applies at EVERY face-data entry point
* D-4: scripts/check_accuracy.py requires explicit credentials (SEC-13)
* DEF-34: download_models.py creates its target dirs and handles the flat
  official zip layout (found when the model step finally ran for real)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.models import Student
from app.services import enrollment

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------ D-1
def test_confirmation_rule_defaults_are_acceptance_3_frames_10s() -> None:
    """Approved decision: CONTRIBUTING's acceptance criterion is authoritative.

    Asserted on the field defaults, not the live settings object, because
    conftest pins CONFIRMATION_FRAMES=1 for test speed.
    """
    fields = settings.model_fields
    assert fields["confirmation_frames"].default == 3
    assert fields["confirmation_window_s"].default == 10.0


# ------------------------------------------------------------------------ D-2
def _force_no_face(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inference answers 'one frame, zero faces' (a real, decidable answer)."""
    import app.inference.client as infc
    from app import quality

    def no_faces(jpegs):
        return {"results": [[]]}

    monkeypatch.setattr(infc, "infer_frames", no_faces)
    monkeypatch.setattr(quality.inference_client, "infer_frames", no_faces)


def _make_student(db) -> Student:
    return enrollment.enroll_student(
        db,
        name="Gate Probe",
        registration_no="STU91001",
        section="CSE-A",
        photos=[b"photo-" + bytes([i]) for i in range(3)],
        skip_quality_checks=True,
    )


def test_add_photos_runs_the_quality_gate(db, fake_embed, monkeypatch) -> None:
    student = _make_student(db)
    _force_no_face(monkeypatch)
    monkeypatch.setattr(settings, "face_quality_enabled", True)

    with pytest.raises(enrollment.EnrollmentError) as excinfo:
        enrollment.add_photos(db, student, [b"blurry"], filenames=["extra.jpg"])
    assert excinfo.value.status_code == 400
    assert "quality check failed" in str(excinfo.value)


def test_replace_face_runs_the_quality_gate(db, fake_embed, monkeypatch) -> None:
    student = _make_student(db)
    _force_no_face(monkeypatch)
    monkeypatch.setattr(settings, "face_quality_enabled", True)

    with pytest.raises(enrollment.EnrollmentError) as excinfo:
        enrollment.replace_face(db, student, [b"blurry-1", b"blurry-2", b"blurry-3"])
    assert excinfo.value.status_code == 400
    assert "quality check failed" in str(excinfo.value)


def test_gate_outage_during_add_photos_is_503_not_400(
    db, fake_embed, monkeypatch
) -> None:
    """BUG-33 semantics must hold on the new gate sites too."""
    import app.inference.client as infc
    from app import quality

    student = _make_student(db)

    def boom(jpegs):
        raise infc.InferenceServiceDown("redis unavailable: connection refused")

    monkeypatch.setattr(infc, "infer_frames", boom)
    monkeypatch.setattr(quality.inference_client, "infer_frames", boom)
    monkeypatch.setattr(settings, "face_quality_enabled", True)

    with pytest.raises(enrollment.InferenceUnavailable):
        enrollment.add_photos(db, student, [b"photo"])


def test_gate_is_skippable_when_disabled(db, fake_embed, monkeypatch) -> None:
    """FACE_QUALITY_ENABLED=false keeps the old fast path (tests pin it off)."""
    student = _make_student(db)
    _force_no_face(monkeypatch)
    monkeypatch.setattr(settings, "face_quality_enabled", False)

    out = enrollment.add_photos(db, student, [b"photo-x"])
    assert len(out.embedding_list) == 4


# ----------------------------------------------------- SEC-04 production checks
_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="  # b64 of 32 zero bytes


def test_production_rejects_wildcard_cors() -> None:
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError) as ei:
        Settings(
            environment="production",
            cors_origins="*",
            embedding_encryption_key=_KEY,
            jwt_secret="y" * 40,
            admin_password="strong-password",
        )
    assert "CORS_ORIGINS" in str(ei.value)


def test_production_rejects_default_admin_password() -> None:
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError) as ei:
        Settings(
            environment="production",
            cors_origins="https://console.example.edu",
            embedding_encryption_key=_KEY,
            jwt_secret="y" * 40,
            admin_password="admin",
        )
    assert "ADMIN_PASSWORD" in str(ei.value)


def test_production_passes_with_explicit_values() -> None:
    from app.config import Settings

    s = Settings(
        environment="production",
        cors_origins="https://console.example.edu",
        embedding_encryption_key=_KEY,
        jwt_secret="y" * 40,
        admin_password="strong-password",
    )
    assert s.environment == "production"


# ------------------------------------------------------------- SEC-10 upload cap
def test_oversized_body_rejected_413(client, auth_header, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 2048)
    files = [("photos", ("big.jpg", b"x" * 4096, "image/jpeg"))]
    resp = client.post(
        "/students",
        data={"name": "Big Body", "registration_no": "STU92001"},
        files=files,
        headers=auth_header,
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.text
    # small bodies still flow
    ok = client.post(
        "/auth/token", json={"username": "admin", "password": "admin"}
    )
    assert ok.status_code == 200


# --------------------------------------------------------- SEC-05 login throttle
def test_login_rate_limit_triggers_429(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    creds = {"username": "admin", "password": "admin"}
    codes = [client.post("/auth/token", json=creds).status_code for _ in range(4)]
    assert codes[0] == 200
    assert 429 in codes, codes


def test_login_rate_limit_disabled_with_zero(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 0)
    codes = [
        client.post(
            "/auth/token", json={"username": "admin", "password": "admin"}
        ).status_code
        for _ in range(6)
    ]
    assert all(c == 200 for c in codes), codes


def test_login_rate_limit_degrades_open_on_redis_outage(client, monkeypatch) -> None:
    """An unavailable cache must never lock the admin out (fail-open here)."""
    import redis as redis_lib

    import app.redis_client as rc

    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)

    def down():
        raise redis_lib.ConnectionError("redis gone")

    monkeypatch.setattr(rc, "get_redis", down)
    codes = [
        client.post(
            "/auth/token", json={"username": "admin", "password": "admin"}
        ).status_code
        for _ in range(3)
    ]
    assert all(c == 200 for c in codes), codes


# ------------------------------------------------------------------------ D-4
def test_check_accuracy_requires_explicit_credentials() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ADMIN_USERNAME", "ADMIN_PASSWORD")
    }
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_accuracy.py")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 2, proc.stderr[-500:]
    assert "ADMIN_USERNAME" in (proc.stderr + proc.stdout)


# ----------------------------------------------------------------------- DEF-34
def _load_download_models():
    import importlib.util

    path = ROOT / "scripts" / "download_models.py"
    spec = importlib.util.spec_from_file_location("download_models_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_fake_pack_zip() -> bytes:
    """A miniature of the official buffalo_l.zip: .onnx files at archive root."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in ("det_10g.onnx", "w600k_r50.onnx"):
            zf.writestr(name, b"O" * 2048)
    return buf.getvalue()


def test_download_models_creates_dirs_even_when_download_fails(
    tmp_path, monkeypatch, capsys
) -> None:
    """Fresh machine + network failure must report the network error, not ENOENT."""
    dm = _load_download_models()

    def no_network(url, timeout=60):
        raise OSError("network down")

    monkeypatch.setattr(dm.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(
        "sys.argv", ["download_models.py", "--root", str(tmp_path)]
    )
    rc = dm.main()
    out = capsys.readouterr()
    assert rc == 1
    assert "DOWNLOAD FAILED" in out.err
    assert "No such file or directory" not in out.err, "zip dir must exist before write"
    assert (tmp_path / "models").is_dir()


def test_download_models_recovers_flat_layout_without_redownload(
    tmp_path, monkeypatch
) -> None:
    """Flat-extracted weights are moved into models/<pack>/ offline (no re-fetch)."""
    dm = _load_download_models()
    monkeypatch.setattr(dm, "_verify", lambda *a, **k: [])

    models = tmp_path / "models"
    models.mkdir(parents=True)
    for name in ("det_10g.onnx", "w600k_r50.onnx"):
        (models / name).write_bytes(b"O" * 2048)

    touched = []

    def no_network(url, timeout=60):
        touched.append(url)
        raise AssertionError("must not re-download when flat files exist")

    monkeypatch.setattr(dm.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(
        "sys.argv", ["download_models.py", "--root", str(tmp_path)]
    )
    rc = dm.main()
    assert rc == 0
    assert not touched, "recovery must happen before any network access"
    assert (models / "buffalo_l" / "det_10g.onnx").is_file()
    assert (models / "buffalo_l" / "w600k_r50.onnx").is_file()


def test_download_models_unpacks_flat_zip_into_pack_dir(
    tmp_path, monkeypatch
) -> None:
    """A fresh download of the real (flat) zip lands in models/<pack>/."""
    import io

    dm = _load_download_models()
    monkeypatch.setattr(dm, "_verify", lambda *a, **k: [])

    class FakeResp(io.BytesIO):
        pass

    payload = _write_fake_pack_zip()
    monkeypatch.setattr(
        dm.urllib.request, "urlopen", lambda url, timeout=60: FakeResp(payload)
    )
    monkeypatch.setattr(
        "sys.argv", ["download_models.py", "--root", str(tmp_path)]
    )
    rc = dm.main()
    assert rc == 0
    pack = tmp_path / "models" / "buffalo_l"
    assert (pack / "det_10g.onnx").is_file()
    assert (pack / "w600k_r50.onnx").is_file()
