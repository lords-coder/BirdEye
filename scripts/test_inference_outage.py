"""Controlled inference-service outage: failure must NOT look like 'no face'.

Stops the shared GPU inference service, then verifies on the RUNNING camera
pipelines:

  1. live-status flips to infer_ok=false with an explicit error - it must NOT
     silently report faces=0 as if the room were empty (fixes the missing-hash
     default-True bug in app/api/cameras.py).
  2. a high-severity pipeline_error alert is raised,
  3. recognition logs do NOT grow during the outage,
  4. after restarting the service, inference recovers on its own with no
     duplicate pipelines and no phantom state.

The inference service is restarted in a finally block (process started for
this validation only).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
PY = sys.executable


def api(method, path, tok=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=None, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            return x.status, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def login():
    r = urllib.request.Request(
        BASE + "/auth/token",
        data=json.dumps({"username": "admin", "password": "admin"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(r).read())["access_token"]


def statuses(tok, ids):
    out = {}
    for cid in ids:
        c, b = api("GET", f"/cameras/{cid}/live-status", tok)
        out[cid] = json.loads(b) if c == 200 else {}
    return out


def log_count():
    from app.db import SessionLocal
    from app.models import RecognitionLog

    with SessionLocal() as db:
        return db.query(RecognitionLog).count()


def alert_count():
    from app.db import SessionLocal
    from app.models import Alert

    with SessionLocal() as db:
        return db.query(Alert).filter(Alert.type == "pipeline_error").count()


def infer_pids():
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'app.inference.server' } | "
         "ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True,
    )
    return [int(x) for x in (out.stdout or "").split() if x.strip().isdigit()]


def start_inference():
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()
    return subprocess.Popen(
        [PY, "-m", "app.inference.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for(cond, label, timeout_s=75, step=4):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if cond():
            return True
        time.sleep(step)
    print(f"  TIMEOUT waiting for: {label}")
    return False


def main():
    tok = login()
    c, b = api("GET", "/cameras", tok)
    ids = [x["id"] for x in json.loads(b)]

    from app import redis_client as rc
    from app.config import settings

    r = rc.get_redis()

    pids = infer_pids()
    print(f"inference service pid(s): {pids}")
    assert pids, "inference service not found"

    logs0, alerts0 = log_count(), alert_count()
    st0 = statuses(tok, ids)
    print(f"baseline: recognition_logs={logs0} pipeline_error_alerts={alerts0}")
    infer_ok0 = {i2: st0[i2].get("infer_ok") for i2 in ids}
    print(f"baseline infer_ok: {infer_ok0}")

    proc = None
    try:
        # ---------------- outage --------------------------------------
        print("\n--- stopping GPU inference service ---")
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True)
        time.sleep(3)
        assert not infer_pids(), "inference service still running after kill"

        print("--- waiting for pipelines to surface the outage ---")
        # A camera whose reader cannot open RTSP makes no RPC calls; its
        # infer_ok still flips false once the live-status hash TTLs (15 s),
        # thanks to the missing-hash fix.  A camera WITH frames keeps calling
        # RPC: BLPOP times out at 15 s per batch, and after 3 consecutive
        # failures a high-severity pipeline_error alert is raised.
        alerted = wait_for(
            lambda: alert_count() > alerts0,
            "pipeline_error alert (3 consecutive RPC failures)",
            timeout_s=90,
        )
        st = statuses(tok, ids)
        outage_visible = {i2: (st[i2].get("infer_ok"), st[i2].get("error")) for i2 in ids}
        print(f"  outage visible: {outage_visible}")
        degraded = any(s["infer_ok"] is False for s in st.values())
        assert degraded, (
            "pipelines never surfaced the inference outage - they may be "
            "silently reporting 'no face'"
        )

        logs1, alerts1 = log_count(), alert_count()
        print(f"\nduring outage: recognition_logs={logs1} (+{logs1 - logs0}) "
              f"pipeline_error_alerts={alerts1} (+{alerts1 - alerts0})")
        assert logs1 == logs0, (
            f"recognition rows written while inference was down ({logs0}->{logs1})"
        )
        assert alerted, (
            "no pipeline_error alert raised (expected after 3 consecutive "
            "RPC failures on a camera with frames)"
        )
        print("  OK: no recognition rows; pipeline_error alert raised")

        # pipelines must stay alive
        for i2 in ids:
            assert st[i2].get("state") == "running", (
                f"cam {i2} left 'running': {st[i2].get('state')}"
            )
        print("  OK: both pipelines stayed 'running'")

    finally:
        # ---------------- recovery ------------------------------------
        print("\n--- restarting GPU inference service ---")
        proc = start_inference()
        print(f"  restarted pid={proc.pid}")

    # wait for warmup (gpu:status fresh)
    fresh = wait_for(
        lambda: (
            (lambda h: (h.get("provider") and
                        abs(time.time() - float(h.get("updated_at", 0))) < 6)
             )(r.hgetall(settings.gpu_status_key))
        ),
        "inference service warm and publishing status",
        timeout_s=120,
    )
    h = r.hgetall(settings.gpu_status_key)
    print(f"  inference status provider={h.get('provider')} "
          f"frames_total={h.get('frames_total')}")

    # wait for pipelines to recover inference
    recovered = wait_for(
        lambda: all(st["infer_ok"] for st in statuses(tok, ids).values()),
        "infer_ok=true on all cameras",
        timeout_s=120,
    )
    st = statuses(tok, ids)
    recovered_ok = {i2: st[i2].get("infer_ok") for i2 in ids}
    print(f"  recovered infer_ok: {recovered_ok}")
    print(f"recovered on its own: {recovered}")

    # no duplicate pipelines
    owners = {i2: r.get(rc.cam_slot_key(i2)) for i2 in range(8)}
    owners = {k: int(v) for k, v in owners.items() if v is not None}
    print(f"slot owners after recovery: {owners}")
    print(f"running set: {sorted(r.smembers(settings.running_cameras_key))}")
    assert sorted(owners.values()) == sorted(ids), "slot collision after recovery"

    print("\nDONE")


if __name__ == "__main__":
    main()
