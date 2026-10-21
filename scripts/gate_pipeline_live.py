"""Start both camera pipelines live and verify the snapshot switches to the
Redis frame bus (no competing RTSP capture).

Also verifies dual-camera concurrency and pipeline recovery semantics.
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"


def req(method, path, tok=None, body=None, timeout=20):
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
    c, raw = req("POST", "/auth/token", body={"username": "admin", "password": "admin"})
    tok = json.loads(raw)["access_token"]
    c, raw = req("GET", "/cameras", tok=tok)
    cams = json.loads(raw)
    ids = [x["id"] for x in cams]
    print(f"Cameras: {ids}")

    print("\n--- start both pipelines ---")
    for cid in ids:
        c, raw = req("POST", f"/cameras/{cid}/start", tok=tok)
        print(f"  camera {cid} start -> HTTP {c}: {raw.decode()[:150]}")

    time.sleep(8)

    print("\n--- pipeline status ---")
    for cid in ids:
        c, raw = req("GET", f"/cameras/{cid}/pipeline/status", tok=tok)
        print(f"  camera {cid} status -> HTTP {c}: {raw.decode()[:250]}")

    print("\n--- snapshot while pipelines claim to run ---")
    for cid in ids:
        c, raw = req("GET", f"/cameras/{cid}/snapshot", tok=tok, timeout=25)
        is_jpeg = raw[:2] == b"\xff\xd8"
        print(f"  camera {cid} snapshot -> HTTP {c} jpeg={is_jpeg} bytes={len(raw)}")
        if c == 503:
            try:
                print(f"      detail: {json.loads(raw)['detail']}")
            except Exception:
                pass

    print("\n--- live-status ---")
    for cid in ids:
        c, raw = req("GET", f"/cameras/{cid}/live-status", tok=tok)
        print(f"  camera {cid} live-status -> HTTP {c}: {raw.decode()[:250]}")

    print("\n--- stop one camera, verify the other still runs ---")
    first = ids[0]
    c, raw = req("POST", f"/cameras/{first}/stop?force=true", tok=tok)
    print(f"  camera {first} stop -> HTTP {c}: {raw.decode()[:150]}")
    time.sleep(3)
    for cid in ids:
        c, raw = req("GET", f"/cameras/{cid}/pipeline/status", tok=tok)
        print(f"  camera {cid} status -> HTTP {c}: {raw.decode()[:250]}")

    print("\n--- cleanup: stop remaining ---")
    for cid in ids[1:]:
        c, raw = req("POST", f"/cameras/{cid}/stop?force=true", tok=tok)
        print(f"  camera {cid} stop -> HTTP {c}: {raw.decode()[:150]}")
    time.sleep(2)
    for cid in ids:
        c, raw = req("GET", f"/cameras/{cid}/pipeline/status", tok=tok)
        print(f"  camera {cid} final -> HTTP {c}: {raw.decode()[:200]}")


if __name__ == "__main__":
    main()
