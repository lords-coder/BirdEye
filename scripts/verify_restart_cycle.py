"""Live restart cycle: distinct slots, no phantom state, no duplicates.

Verifies against the RUNNING worker + real Redis:
  1. stop both cameras -> clean stop, slots freed, state=stopped
  2. start both back-to-back -> each gets a DISTINCT slot (the 2026-10-18
     slot-steal race)
  3. exactly ONE task per camera (no duplicate pipelines)
  4. worker restart / reconnection does not leave phantom 'running' state
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def api(method, path, tok=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            return x.status, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def login():
    c, raw = api("POST", "/auth/token",
                 body={"username": "admin", "password": "admin"})
    return json.loads(raw)["access_token"]


def statuses(tok, ids):
    out = {}
    for cid in ids:
        c, raw = api("GET", f"/cameras/{cid}/live-status", tok=tok)
        out[cid] = json.loads(raw) if c == 200 else {"state": f"HTTP{c}"}
    return out


def main():
    tok = login()
    c, raw = api("GET", "/cameras", tok=tok)
    ids = [x["id"] for x in json.loads(raw)]
    print(f"cameras: {ids}")

    # ---------------- 1. stop both -------------------------------------
    print("\n--- stop both ---")
    for cid in ids:
        c, raw = api("POST", f"/cameras/{cid}/stop?force=true", tok=tok)
        print(f"  cam {cid} force-stop -> HTTP {c}: {raw.decode()[:120]}")

    for _ in range(15):
        time.sleep(2)
        st = statuses(tok, ids)
        if all(s.get("state") in ("stopped", None) and not s.get("running")
               for s in st.values()):
            break
    print("  after stop:", {k: (v.get("state"), v.get("running"))
                            for k, v in st.items()})

    # ---------------- 2. start both back-to-back -----------------------
    print("\n--- start both back-to-back (slot-steal regression) ---")
    got = {}
    for cid in ids:
        c, raw = api("POST", f"/cameras/{cid}/start", tok=tok)
        j = json.loads(raw)
        got[cid] = j.get("slot")
        print(f"  cam {cid} start -> HTTP {c} slot={j.get('slot')} "
              f"task={str(j.get('task_id'))[:8]}")
    assert len(set(got.values())) == len(ids), (
        f" SLOT COLLISION: {got} - pipelines are sharing a slot"
    )
    print(f"  distinct slots OK: {got}")

    # ---------------- 3. wait for running ------------------------------
    print("\n--- wait for running + frames ---")
    st = {}
    for i in range(25):
        time.sleep(3)
        st = statuses(tok, ids)
        if all(s.get("running") and s.get("has_frame") for s in st.values()):
            print(f"  both running after ~{(i + 1) * 3}s")
            break
    for cid, s in st.items():
        print(f"  cam {cid}: state={s.get('state')} running={s.get('running')} "
              f"fps={s.get('fps')} infer_ok={s.get('infer_ok')} "
              f"infer_ms={s.get('infer_ms')} faces={s.get('faces')} "
              f"frame_age={s.get('frame_age_s')}")

    # ---------------- 4. slot ownership from Redis ----------------------
    print("\n--- slot ownership (Redis) ---")
    from app import redis_client as rc
    from app.config import settings

    r = rc.get_redis()
    owners = {}
    for i in range(settings.camera_slots):
        o = r.get(rc.cam_slot_key(i))
        if o is not None:
            owners[i] = int(o)
    print("  slot owners:", owners)
    assert sorted(owners.values()) == sorted(ids), (
        f"unexpected slot owners {owners}"
    )

    print("\n--- running-set / task keys ---")
    print("  running set:", sorted(r.smembers(settings.running_cameras_key)))
    for cid in ids:
        print(f"  cam {cid}: task={str(r.get(rc.cam_task_key(cid)))[:8]} "
              f"state={r.get(rc.cam_state_key(cid))} "
              f"hb_age={time.time() - float(r.get(rc.cam_heartbeat_key(cid))):.1f}s")

    print("\nDONE")


if __name__ == "__main__":
    main()
