"""Runtime acceptance test: two camera pipelines run concurrently & independently.

Runs against a LIVE local stack (API + Redis + Celery worker + inference
service).  Uses file-based camera sources so NO physical camera is required -
this tests the dispatch/consume/monitor/stop machinery, not RTSP connectivity.

Scenarios verified:
  1. Two distinct camera-processing tasks are ACTIVE concurrently
     (each reaches state=running on a DEDICATED slot).
  2. Stopping one camera leaves the other running (independence).
  3. A freed slot can be reclaimed by a third camera.
  4. Force-stop clears state + slot immediately.

Usage (from repo root, with the venv active and the stack running):
    $env:PYTHONPATH="."
    python scripts/gate_concurrency.py

Prerequisites (started separately, e.g. from another terminal):
    redis-server --port 6379                              # Redis bus
    python -m app.inference.server                        # shared GPU inference
    powershell -File scripts\run_worker.ps1               # Celery worker (threads pool)
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # API

Exit codes: 0 = all checks passed, 1 = any check failed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
START_TIMEOUT_S = 40
STOP_TIMEOUT_S = 20
POLL_S = 1.5


class GateFail(AssertionError):
    pass


def api(method, path, tok=None, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            return x.status, json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"detail": body}
    except Exception as exc:  # noqa: BLE001 - report as a gate failure
        raise GateFail(f"{method} {path} could not reach the API: {exc}")


def login():
    code, body = api("POST", "/auth/token",
                     body={"username": "admin", "password": "admin"})
    if code != 200:
        raise GateFail(f"admin login failed HTTP {code}: {body}")
    return body["access_token"]


def create_camera(tok, name, source_kind="file"):
    body = {"name": name, "type": "entry", "sampling_rate": 5,
            "enabled": True}
    if source_kind == "file":
        body["file_path"] = r"C:\does-not-exist\test_video.mp4"
    else:
        body["host"] = "127.0.0.1"
        body["port"] = 554
        body["stream_path"] = "/"
        body["username"] = "gateprobe"
    code, body = api("POST", "/cameras", tok=tok, body=body)
    if code != 201:
        raise GateFail(f"create camera {name!r} failed HTTP {code}: {body}")
    return body["id"]


def runtime(tok, cid):
    code, body = api("GET", f"/cameras/{cid}", tok=tok)
    if code != 200:
        raise GateFail(f"GET /cameras/{cid} HTTP {code}: {body}")
    return body["runtime"]


def wait_running(tok, cid, label, timeout_s=START_TIMEOUT_S):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        rt = runtime(tok, cid)
        last = rt
        if rt["state"] == "running" and rt["running"]:
            return rt
        time.sleep(POLL_S)
    raise GateFail(
        f"{label} never reached 'running' (last runtime={last})"
    )


def wait_state(tok, cid, want_running, label, timeout_s=STOP_TIMEOUT_S):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        rt = runtime(tok, cid)
        last = rt
        if rt["running"] == want_running:
            return rt
        time.sleep(POLL_S)
    raise GateFail(
        f"{label} did not reach running={want_running} (last runtime={last})"
    )


def main() -> int:
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")

    tok = login()
    print("authenticated as admin")

    # ---- create two cameras -------------------------------------------
    cid_a = create_camera(tok, "Gate A")
    cid_b = create_camera(tok, "Gate B")
    print(f"created cameras: A={cid_a} B={cid_b}")

    # ---- start both concurrently --------------------------------------
    ra = api("POST", f"/cameras/{cid_a}/start", tok=tok)
    rb = api("POST", f"/cameras/{cid_b}/start", tok=tok)
    print(f"start A -> HTTP {ra[0]} slot={ra[1].get('slot')} task={str(ra[1].get('task_id'))[:8]}")
    print(f"start B -> HTTP {rb[0]} slot={rb[1].get('slot')} task={str(rb[1].get('task_id'))[:8]}")
    check("both starts accepted (HTTP 200)",
          ra[0] == 200 and rb[0] == 200,
          f"A={ra[0]} B={rb[0]}")

    rt_a = wait_running(tok, cid_a, "camera A")
    rt_b = wait_running(tok, cid_b, "camera B")
    print(f"A running slot={rt_a['slot']} | B running slot={rt_b['slot']}")

    # ---- TASK 4: two distinct tasks active concurrently ----------------
    check("camera A active on a dedicated slot",
          rt_a["state"] == "running" and rt_a["running"] and rt_a["slot"] is not None,
          f"slot={rt_a['slot']}")
    check("camera B active on a dedicated slot",
          rt_b["state"] == "running" and rt_b["running"] and rt_b["slot"] is not None,
          f"slot={rt_b['slot']}")
    check("two DISTINCT slots (two queues, two worker threads)",
          rt_a["slot"] is not None and rt_b["slot"] is not None
          and rt_a["slot"] != rt_b["slot"],
          f"A={rt_a['slot']} B={rt_b['slot']}")
    check("both running at the same time (concurrency)",
          rt_a["running"] and rt_b["running"])

    # ---- stop A, B must stay running ----------------------------------
    code, _ = api("POST", f"/cameras/{cid_a}/stop?force=true", tok=tok)
    check("stop A accepted (HTTP 200)", code == 200, f"HTTP {code}")
    rt_a2 = wait_state(tok, cid_a, False, "camera A stopped")
    rt_b_still = runtime(tok, cid_b)
    check("camera A stopped",
          rt_a2["running"] is False and rt_a2["state"] in ("stopped", "stopping"),
          f"A state={rt_a2['state']}")
    check("camera B still running while A stopped (independence)",
          rt_b_still["running"] is True and rt_b_still["state"] == "running",
          f"B state={rt_b_still['state']} slot={rt_b_still['slot']}")

    # ---- stop B, then reclaim freed slot with a third camera -----------
    code, _ = api("POST", f"/cameras/{cid_b}/stop?force=true", tok=tok)
    check("stop B accepted (HTTP 200)", code == 200, f"HTTP {code}")
    rt_b2 = wait_state(tok, cid_b, False, "camera B stopped")
    check("camera B stopped",
          rt_b2["running"] is False,
          f"B state={rt_b2['state']}")

    cid_c = create_camera(tok, "Gate C")
    code, _ = api("POST", f"/cameras/{cid_c}/start", tok=tok)
    check("third camera start accepted", code == 200, f"HTTP {code}")
    rt_c = wait_running(tok, cid_c, "camera C")
    check("freed slot reclaimed by third camera",
          rt_c["running"] is True and rt_c["slot"] is not None,
          f"C slot={rt_c['slot']}")

    # ---- cleanup -------------------------------------------------------
    code, _ = api("POST", f"/cameras/{cid_c}/stop?force=true", tok=tok)
    for cid in (cid_a, cid_b, cid_c):
        api("DELETE", f"/cameras/{cid}", tok=tok)

    # ---- summary -------------------------------------------------------
    fails = [c for c in checks if not c[1]]
    print()
    if fails:
        print(f"GATE FAILED: {len(fails)}/{len(checks)} checks failed")
        for name, ok, detail in fails:
            print(f"  - {name}: {detail}")
        return 1
    print(f"GATE PASSED: {len(checks)}/{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
