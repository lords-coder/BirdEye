"""Live end-to-end proof of gate 3 (snapshot/stream reuse pipeline frames).

Writes a sentinel frame into the SAME Redis the app uses, marks the camera
running, then asks the LIVE server for a snapshot and the legacy stream.
If the served bytes are the sentinel, the endpoint read from the frame bus
and opened NO camera connection.

Cleans up every key it creates.
"""
import json
import time
import urllib.request
import urllib.error

import cv2
import numpy as np
import redis

from app import redis_client as rc
from app.config import settings
from app.db import SessionLocal
from app.models import Camera

BASE = "http://127.0.0.1:8765"


def api(method, path, tok=None, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if tok:
        r.add_header("Authorization", f"Bearer {tok}")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def sentinel_jpeg():
    img = np.full((64, 96, 3), (0, 200, 0), dtype=np.uint8)
    cv2.putText(img, "FRAMEBUS", (3, 40), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def main():
    c, raw = api("POST", "/auth/token",
                 body={"username": "admin", "password": "admin"})
    tok = json.loads(raw)["access_token"]

    db = SessionLocal()
    cam = db.query(Camera).first()
    cid = cam.id
    db.close()

    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    r_bin = redis.Redis.from_url(settings.redis_url, decode_responses=False)
    print(f"Camera id={cid}; app Redis reachable: {r.ping()}")

    payload = sentinel_jpeg()
    print(f"Sentinel JPEG: {len(payload)} bytes, md5={__import__('hashlib').md5(payload).hexdigest()[:12]}")

    created = []
    try:
        # --- simulate a running pipeline -------------------------------
        r.set(rc.cam_state_key(cid), "running", ex=60); created.append(rc.cam_state_key(cid))
        r.set(rc.cam_heartbeat_key(cid), str(time.time()), ex=60)
        created.append(rc.cam_heartbeat_key(cid))
        rc.publish_live_frame(r_bin, r, cid, payload, frame_ts=time.time(),
                              fps=6.0, infer_ok=True, infer_ms=3.0,
                              faces=1, error=None, ttl_s=30)
        created.append(rc.cam_frame_key(cid))
        created.append(rc.cam_live_key(cid))

        print("\n--- GATE 3a: snapshot while 'running' ---")
        c, body = api("GET", f"/cameras/{cid}/snapshot", tok=tok)
        served_sentinel = body == payload
        print(f"  HTTP {c}; bytes={len(body)}; served_sentinel={served_sentinel}")
        if not served_sentinel:
            print(f"  first bytes: {body[:20]!r}")

        print("\n--- GATE 3b: legacy stream.mjpeg while 'running' ---")
        try:
            rq = urllib.request.Request(
                BASE + f"/cameras/{cid}/stream.mjpeg", method="GET")
            rq.add_header("Authorization", f"Bearer {tok}")
            with urllib.request.urlopen(rq, timeout=6) as x:
                chunk = x.read(65536)
                print(f"  HTTP {x.status}; ct={x.headers.get('content-type')}")
                print(f"  contains sentinel bytes: {payload[:32] in chunk}")
                print(f"  multipart boundary present: {b'--frame' in chunk}")
        except Exception as e:  # noqa: BLE001
            print(f"  stream -> {type(e).__name__}: {e}")

        print("\n--- GATE 3c: running but frame bus EMPTY -> 503, no probe ---")
        r_bin.delete(rc.cam_frame_key(cid))
        c, body = api("GET", f"/cameras/{cid}/snapshot", tok=tok)
        print(f"  HTTP {c}: {body.decode()[:160]}")

    finally:
        print("\n--- cleanup ---")
        for k in created:
            try:
                r.delete(k)
                print(f"  deleted {k}")
            except Exception as e:  # noqa: BLE001
                print(f"  could not delete {k}: {e}")
        # safety: ensure we did not leave a phantom 'running' camera
        st = r.get(rc.cam_state_key(cid))
        print(f"  camera {cid} state after cleanup: {st!r}")


if __name__ == "__main__":
    main()
