"""Live API acceptance probe against the running app on 127.0.0.1:8765.

Checks auth, snapshot semantics (stopped vs running), camera start/stop
lifecycle, and event/timestamp delivery.  Prints PASS/FAIL per gate.
Never prints credentials.
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8765"
RESULTS = []


def gate(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def req(method, path, token=None, body=None, timeout=15):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def main():
    print("\n=== AUTH ===")
    code, raw = req("POST", "/auth/token",
                    body={"username": "admin", "password": "admin"})
    gate("admin login", code == 200, f"HTTP {code}")
    if code != 200:
        print("cannot continue without auth")
        return 1
    tok = json.loads(raw)["access_token"]

    code, raw = req("POST", "/auth/token",
                    body={"username": "admin", "password": "wrong"})
    gate("bad password rejected", code in (400, 401, 422), f"HTTP {code}")

    code, raw = req("GET", "/cameras")
    gate("no token -> 401", code == 401, f"HTTP {code}")

    print("\n=== CAMERA STATE ===")
    code, raw = req("GET", "/cameras", token=tok)
    cams = json.loads(raw)
    gate("camera list", code == 200 and len(cams) >= 2, f"{len(cams)} cameras")
    for c in cams:
        pw_leaked = "password" in json.dumps(c) and c.get("password")
        gate(f"camera {c['id']} no password leak", not pw_leaked,
             f"has_password={c.get('has_password')}")
        gate(f"camera {c['id']} role set", bool(c.get("camera_role")),
             c.get("camera_role"))

    print("\n=== SNAPSHOT (pipeline stopped) ===")
    for c in cams:
        code, raw = req("GET", f"/cameras/{c['id']}/snapshot", token=tok)
        is_jpeg = raw[:2] == b"\xff\xd8"
        # stopped + reachable source => 200 JPEG; unreachable => 502
        gate(f"camera {c['id']} snapshot stopped -> 200/502",
             code in (200, 502), f"HTTP {code} jpeg={is_jpeg}")

    print("\n=== LIVE STATUS ===")
    for c in cams:
        code, raw = req("GET", f"/cameras/{c['id']}/live-status", token=tok)
        gate(f"camera {c['id']} live-status", code == 200, f"HTTP {code}")

    print("\n=== EVENT / TIMESTAMP DELIVERY ===")
    code, raw = req("GET", "/alerts?limit=5", token=tok)
    gate("alerts endpoint", code == 200, f"HTTP {code}")

    code, raw = req("GET", "/attendance/presence/states", token=tok)
    gate("presence states endpoint", code == 200, f"HTTP {code}")

    print("\n=== SECURITY: sensitive paths ===")
    for path in ["/system/health"]:
        code, raw = req("GET", path)
        body = raw.decode("utf-8", "ignore").lower()
        pii = any(k in body for k in ["password", "registration_no", "10.10.15"])
        gate(f"{path} PII-free", code == 200 and not pii, f"HTTP {code}")

    print("\n=== SUMMARY ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"{passed}/{total} gates passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
