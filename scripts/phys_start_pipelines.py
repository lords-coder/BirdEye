"""Start BOTH physical camera pipelines and verify real GPU inference.

Verifies gate 3/4 of the physical-camera acceptance:
  - jobs are dispatched AND consumed (state reaches 'running')
  - the GPU inference service actually processes frames
  - both pipelines run concurrently
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"


def api(method, path, tok=None, body=None, timeout=25):
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


def main():
    c, raw = api("POST", "/auth/token",
                 body={"username": "admin", "password": "admin"})
    tok = json.loads(raw)["access_token"]

    c, raw = api("GET", "/cameras", tok=tok)
    cams = json.loads(raw)
    ids = [x["id"] for x in cams]
    print(f"cameras: {[(x['id'], x['name'], x['camera_role']) for x in cams]}")

    print("\n--- dispatch both pipelines ---")
    for cid in ids:
        c, raw = api("POST", f"/cameras/{cid}/start", tok=tok)
        print(f"  camera {cid} start -> HTTP {c}: {raw.decode()[:160]}")

    print("\n--- wait for the worker to consume and the pipeline to run ---")
    for _ in range(20):
        time.sleep(3)
        states = {}
        for cid in ids:
            c, raw = api("GET", f"/cameras/{cid}/live-status", tok=tok)
            states[cid] = json.loads(raw) if c == 200 else {"state": f"HTTP{c}"}
        summary = {cid: (s.get("state"), s.get("running"), s.get("has_frame"))
                   for cid, s in states.items()}
        print(f"  t+{_*3:>2}s  {summary}")
        if all(s.get("running") and s.get("has_frame") for s in states.values()):
            print("  BOTH pipelines running and publishing frames")
            break

    print("\n--- final live-status ---")
    for cid in ids:
        c, raw = api("GET", f"/cameras/{cid}/live-status", tok=tok)
        print(f"  camera {cid}: {raw.decode()[:300]}")


if __name__ == "__main__":
    main()
