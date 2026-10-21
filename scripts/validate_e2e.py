#!/usr/bin/env python
"""End-to-end acceptance driver against a RUNNING stack (Stage 5 validation).

Requires: Redis, the API (uvicorn), the Celery worker and the inference
service already running, e.g.:

    redis-server                       # any Redis 5+ on redis://127.0.0.1:6379/0
    python -m app.inference.server     # shared inference (RPC over Redis)
    uvicorn app.main:app --port 8765
    celery -A app.workers.celery_app:celery_app worker --pool=threads \
        --queues=control,jobs,camera_slot_0,camera_slot_1

    ADMIN_USERNAME=... ADMIN_PASSWORD=... \
        python scripts/validate_e2e.py --base-url http://127.0.0.1:8765

What it proves with REAL components (real model, real Redis RPC, real
Celery pipeline, real SQLite rows):

  1  /system/health reports redis + inference + db
  2  enrollment runs the real quality gate and creates a student
  3  duplicate-identity rejection (same human, second registration) -> 409
  4  a second, DIFFERENT identity enrolls cleanly
  5  a file-source camera runs the real pipeline: recognition events,
     open attendance sessions, summary "present", WebSocket live events
  6  clean camera stop and (by default) full cleanup of validation data

The faces are PUBLIC-DOMAIN sample images (Obama / two-people / astronaut
from public example repos) - never real student data.  Exit code 0 = all
steps passed; 1 = at least one failure.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date, datetime, time as dtime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

SAMPLES = {
    "obama.jpg": "https://raw.githubusercontent.com/ageitgey/face_recognition"
                 "/master/examples/obama.jpg",
    "two_people.jpg": "https://raw.githubusercontent.com/ageitgey/"
                      "face_recognition/master/examples/two_people.jpg",
    "astronaut.png": "https://raw.githubusercontent.com/scikit-image/"
                     "scikit-image/v0.19.3/skimage/data/astronaut.png",
}

PASS: list[str] = []
FAIL: list[str] = []


def ok(msg: str) -> None:
    PASS.append(msg)
    print(f"  PASS  {msg}")


def bad(msg: str) -> None:
    FAIL.append(msg)
    print(f"  FAIL  {msg}", file=sys.stderr)


def fetch(name: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    if not path.exists():
        urllib.request.urlretrieve(SAMPLES[name], path)
    return path


def face_variants(img_bytes: bytes, n: int = 3) -> list[tuple[str, bytes]]:
    """n JPEG variants of the SAME photo (flip/zoom/brightness).

    The quality gate and the recognition engine both run on these for real;
    variants of one source image share an identity, which is exactly what
    enrollment and the duplicate check need.
    """
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    out: list[tuple[str, bytes]] = []
    for i in range(n):
        v = img.copy()
        if i % 2 == 1:
            v = cv2.flip(v, 1)
        if i % 3 == 2:
            # Mild zoom anchored to the TOP of the frame - faces usually sit
            # high, so a center crop would cut the face and the (correct)
            # quality gate rejects it.
            v = cv2.resize(v[0:int(h * 0.92), int(w * 0.05):int(w * 0.95)],
                           (w, h))
        if i == 3:
            v = cv2.convertScaleAbs(v, alpha=1.1, beta=12)
        okk, buf = cv2.imencode(".jpg", v, [cv2.IMWRITE_JPEG_QUALITY, 92])
        out.append((f"variant_{i}.jpg", buf.tobytes() if okk else v.tobytes()))
    return out


def crop_face_region(img_bytes: bytes, xr: tuple[float, float],
                     yr: tuple[float, float]) -> bytes:
    """Crop a face region (fractions of width/height) and re-encode JPEG."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    crop = img[int(h * yr[0]):int(h * yr[1]), int(w * xr[0]):int(w * xr[1])]
    okk, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return buf.tobytes() if okk else crop.tobytes()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    ap.add_argument("--keep-data", action="store_true",
                    help="skip cleanup of the validation students/camera")
    args = ap.parse_args()

    user = os.environ.get("ADMIN_USERNAME")
    pw = os.environ.get("ADMIN_PASSWORD")
    if not user or not pw:
        print("ADMIN_USERNAME / ADMIN_PASSWORD must be exported explicitly",
              file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    client = httpx.Client(base_url=base, timeout=300.0)
    asset = Path(tempfile.gettempdir()) / "opencode" / "e2e"
    evidence: dict = {"started_at": datetime.utcnow().isoformat() + "Z"}

    # ------------------------------------------------------------ 1 health
    print("[1] health")
    h = client.get("/system/health").json()
    print("    " + json.dumps(h, indent=None)[:300])
    evidence["health"] = h
    checks = h.get("checks", h)
    if checks.get("database") == "up":
        ok("health: database reachable")
    else:
        bad(f"health: database not up ({h})")
    if checks.get("redis") == "up":
        ok("health: redis reachable")
    else:
        bad(f"health: redis not up ({h}) - is redis running?")
    if checks.get("inference") == "online":
        ok("health: inference service up")
    else:
        bad(f"health: inference not online ({h}) - is "
            "`python -m app.inference.server` running?")

    # ------------------------------------------------------------ 2 login
    print("[2] login")
    r = client.post("/auth/token", json={"username": user, "password": pw})
    if r.status_code != 200:
        bad(f"login failed: HTTP {r.status_code} {r.text[:200]}")
        return 1
    tok = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    ok("login accepted")

    # ----------------------------------------------------------- 3 period
    print("[3] period covering now (local, all weekdays)")
    periods = client.get("/periods", headers=auth).json()
    per = next((p for p in periods if p["name"] == "E2E Validation Period"), None)
    if per is None:
        r = client.post(
            "/periods", headers=auth,
            json={"name": "E2E Validation Period",
                  "start_time": "00:00:00", "end_time": "23:59:59",
                  "days_bitmask": 127},
        )
        if r.status_code == 201:
            per = r.json()
            ok(f"period created (id={per['id']})")
        else:
            bad(f"period create: HTTP {r.status_code} {r.text[:200]}")
            return 1
    else:
        ok(f"period already present (id={per['id']})")

    # ------------------------------------------------------- 4 face assets
    print("[4] public-domain face assets")
    obama = fetch("obama.jpg", asset).read_bytes()
    two = fetch("two_people.jpg", asset).read_bytes()
    a_photos = face_variants(obama, 3)
    # identity B: the non-Obama subject in two_people.jpg (right half);
    # fallback to the astronaut portrait if the crop fails the gate.
    b_raw = crop_face_region(two, (0.5, 1.0), (0.0, 1.0))
    ok("face assets ready (obama, two_people crop; astronaut as fallback)")

    def enroll(reg: str, name: str, photos: list[tuple[str, bytes]]):
        files = [("photos", p) for p in photos]
        return client.post(
            "/students", headers=auth,
            data={"name": name, "registration_no": reg, "section": "E2E"},
            files=files,
        )

    # ------------------------------------------------------- 5 enroll A/B
    print("[5] enrollment through the REAL quality gate")
    r = enroll("STU-E2E-A", "E2E Validation Face A", a_photos)
    if r.status_code == 201:
        sid_a = r.json()["id"]
        ok(f"student A enrolled (id={sid_a}, real quality gate + embeddings)")
    else:
        bad(f"enroll A: HTTP {r.status_code} {r.text[:300]}")
        return 1

    print("[6] duplicate-identity rejection (same face, new registration)")
    r = enroll("STU-E2E-DUP", "E2E Duplicate Probe", a_photos)
    if r.status_code == 409:
        ok(f"duplicate rejected: 409 ({r.json().get('detail', '')[:120]})")
    else:
        bad(f"duplicate probe: expected 409, got {r.status_code} {r.text[:200]}")

    print("[7] second identity enrollment")
    b_photos = face_variants(b_raw, 3)
    r = enroll("STU-E2E-B", "E2E Validation Face B", b_photos)
    sid_b = None
    if r.status_code != 201:
        # fallback: astronaut portrait is a third, distinct identity
        print("    two_people crop rejected by gate -> astronaut fallback")
        try:
            astro_png = fetch("astronaut.png", asset).read_bytes()
            b_photos = face_variants(astro_png, 3)
            r = enroll("STU-E2E-B", "E2E Validation Face B", b_photos)
        except Exception as exc:  # noqa: BLE001
            print(f"    astronaut fallback unavailable: {exc}")
    if r.status_code == 201:
        sid_b = r.json()["id"]
        ok(f"student B enrolled (id={sid_b})")
    else:
        bad(f"enroll B: HTTP {r.status_code} {r.text[:300]}")
        return 1

    # --------------------------------------------------------- 8 test video
    print("[8] build recognition test video from enrolled photos")
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_test_video.py")],
        capture_output=True, text=True, timeout=600,
    )
    video = ROOT / "tests" / "assets" / "test_multi.mp4"
    if proc.returncode == 0 and video.is_file():
        ok(f"test video built ({video.stat().st_size // 1024} KB)")
    else:
        bad(f"make_test_video: rc={proc.returncode} {proc.stderr[-300:]}")
        return 1

    # ------------------------------------------------- 9 camera + WS + run
    print("[9] file-source camera runs the REAL pipeline")
    cams = client.get("/cameras", headers=auth).json()
    cam = next((c for c in cams if c["name"] == "E2E Validation Camera"), None)
    if cam is None:
        r = client.post("/cameras", headers=auth, json={
            "name": "E2E Validation Camera",
            "location": "validation rig (file source)",
            "file_path": str(video),
            "type": "both",
            "sampling_rate": 5,
        })
        if r.status_code == 201:
            cam = r.json()
            ok(f"camera created (id={cam['id']}, file source)")
        else:
            bad(f"camera create: HTTP {r.status_code} {r.text[:300]}")
            return 1
    else:
        ok(f"camera already present (id={cam['id']})")
    cam_id = cam["id"]

    ws_events: list[dict] = []
    ws_done = threading.Event()

    def ws_collector() -> None:
        try:
            import websockets.sync.client as wsc
            with wsc.connect(
                f"ws://127.0.0.1:{base.split('//')[1].split(':')[1].split('/')[0]}"
                f"/ws/live-feed?token={tok}", open_timeout=10,
            ) as ws:
                while not ws_done.is_set():
                    try:
                        ws_events.append(json.loads(ws.recv(timeout=2)))
                    except TimeoutError:
                        continue
        except Exception as exc:  # noqa: BLE001
            ws_events.append({"_error": str(exc)})

    ws_thread = threading.Thread(target=ws_collector, daemon=True)
    ws_thread.start()
    time.sleep(1.0)

    r = client.post(f"/cameras/{cam_id}/start", headers=auth)
    if r.status_code in (200, 202):
        ok(f"camera start accepted ({r.json().get('state')}, "
           f"slot={r.json().get('slot')}, task={r.json().get('task_id')})")
    else:
        bad(f"camera start: HTTP {r.status_code} {r.text[:300]}")

    deadline = time.time() + 150
    seen_logs = 0
    sessions: list[dict] = []
    while time.time() < deadline:
        time.sleep(5)
        logs = client.get("/attendance/logs", headers=auth,
                          params={"page": 1, "page_size": 50}).json()
        seen_logs = logs.get("total", 0)
        sessions = client.get(
            "/attendance/sessions", headers=auth,
            params={"date": date.today().isoformat(), "status": "ongoing"},
        ).json() if _has_sessions_route(client) else []
        print(f"    ... logs total={seen_logs}, ongoing sessions="
              f"{len(sessions)}")
        students_seen = {
            lg.get("student_id") for lg in logs.get("items", logs.get("logs", []))
        }
        if seen_logs >= 2 and {sid_a, sid_b} <= students_seen:
            break
    evidence["logs_total"] = seen_logs
    logs = client.get("/attendance/logs", headers=auth,
                      params={"page": 1, "page_size": 50}).json()
    students_seen = {
        lg.get("student_id") for lg in logs.get("items", logs.get("logs", []))
    }
    if {sid_a, sid_b} <= students_seen:
        ok(f"recognition events for BOTH identities (logs total={seen_logs})")
    elif seen_logs > 0:
        bad(f"only partial recognition: saw {students_seen}, expected "
            f"{{{sid_a}, {sid_b}}}")
    else:
        bad("no recognition events after 150s of pipeline runtime")

    if sessions:
        ok(f"attendance sessions opened ({len(sessions)} ongoing)")
    else:
        bad("no ongoing attendance session after recognition")

    summary = client.get(
        "/attendance/summary", headers=auth,
        params={"date": date.today().isoformat()},
    ).json()
    evidence["summary"] = summary
    # shape: {date, timezone, students: [{registration_no, entries, ongoing,
    # ...}], totals: {present_students, ...}} - no per-row status field
    rows = summary.get("students", [])
    present_regs = {
        row.get("registration_no") for row in rows
        if row.get("entries", 0) >= 1
    }
    n_present = summary.get("totals", {}).get("present_students", 0)
    if {"STU-E2E-A", "STU-E2E-B"} <= present_regs and n_present >= 2:
        ok(f"summary marks both validation students PRESENT "
           f"(present_students={n_present})")
    else:
        bad(f"summary present set {present_regs} "
            f"(present_students={n_present}) missing validation students")

    ws_done.set()
    ws_thread.join(timeout=5)
    real_ws = [e for e in ws_events if "type" in e and "_error" not in e]
    evidence["ws_events"] = len(real_ws)
    if real_ws:
        kinds = sorted({e.get("type") for e in real_ws})
        ok(f"WebSocket live feed delivered {len(real_ws)} events (types={kinds})")
    else:
        bad(f"no WebSocket events received ({ws_events[:2]})")

    # ------------------------------------------------------------ 10 stop
    print("[10] clean camera stop")
    r = client.post(f"/cameras/{cam_id}/stop", headers=auth)
    stop_state = None
    if r.status_code in (200, 202):
        stop_state = r.json().get("state")
    stopped = False
    for _ in range(30):
        time.sleep(2)
        rt = client.get(f"/cameras/{cam_id}", headers=auth).json()
        if rt.get("runtime", {}).get("running") is False:
            stopped = True
            break
    if stopped:
        ok(f"camera stopped cleanly (state={stop_state})")
    else:
        bad("camera still running 60s after stop request")

    # ---------------------------------------------------------- 11 cleanup
    if not args.keep_data:
        print("[11] cleanup of validation data")
        for sid in (sid_a, sid_b):
            if sid:
                client.delete(f"/students/{sid}", headers=auth)
        client.delete(f"/cameras/{cam_id}", headers=auth)
        client.delete(f"/periods/{per['id']}", headers=auth)
        left = client.get("/students", headers=auth).json()
        n_left = len(left if isinstance(left, list) else left.get("items", []))
        if n_left == 0:
            ok("cleanup complete (no validation students remain)")
        else:
            warn_msg = f"{n_left} student(s) remain after cleanup"
            print(f"  WARN  {warn_msg}")
            evidence["cleanup_warning"] = warn_msg
    else:
        print("[11] --keep-data: validation rows retained")

    evidence["finished_at"] = datetime.utcnow().isoformat() + "Z"
    ev_path = Path(tempfile.gettempdir()) / "opencode" / "e2e_evidence.json"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps(evidence, indent=2, default=str))
    print(f"\nevidence written to {ev_path}")
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


def _has_sessions_route(client: httpx.Client) -> bool:
    return True  # /attendance/sessions exists (see app/api/attendance.py)


if __name__ == "__main__":
    raise SystemExit(main())
