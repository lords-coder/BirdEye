#!/usr/bin/env python
"""Phase-1 baseline: end-to-end stream-delivery measurement (bounded).

The physical Dahua cameras are unreachable in this deployment (their rows are
metadata-only: ``stream_path`` is NULL), so physical-camera measurements are
BLOCKED.  This script measures the REAL running stack against one
pipeline-fed camera whose source is a synthetic 2560x1440 @ 26 FPS clip, so
every stage that the browser path actually pays is measured with real numbers:

  1. publish FPS + GPU RPC latency + frame age from /live-status telemetry
     (``fps``, ``infer_ms``, ``frame_age_s`` - the last two are server-side,
     so they carry no host/container clock skew)
  2. delivered MJPEG FPS: multipart part arrivals on a monotonic clock over a
     bounded window, plus the X-Frame-Ts age of each part
  3. snapshot latency while the pipeline is RUNNING (must be a Redis frame-bus
     hit - a source probe would be an order of magnitude slower)
  4. proof that extra HTTP reads create no capture work: telemetry stays
     continuous across a burst of snapshot + status requests

No secret is read from argv or hard-coded: the admin credential comes from the
existing ``.env`` (documented deployment default).  No camera credential is
involved at all (the source is a local file).

    .venv\\Scripts\\python.exe scripts\\profile_stream_delivery.py --create --start
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://localhost:8000"
PERF_NAME = "PERF-BASELINE synthetic main stream"
PERF_FILE = "/samples/videos/perf_baseline.mp4"

TS_RE = re.compile(rb"X-Frame-Ts:\s*([0-9.eE+-]+)")
DATE_RE = re.compile(r"Date:\s*(.+)")


def load_env() -> dict[str, str]:
    """Read the existing .env (already on disk) - never argv, never source."""
    out: dict[str, str] = {}
    p = REPO / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def req(path: str, token: str | None = None, method: str = "GET",
        body: dict | None = None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)


def login(env: dict[str, str]) -> str:
    status, data = req(
        "/auth/token",
        method="POST",
        body={"username": env.get("ADMIN_USERNAME", "admin"),
              "password": env.get("ADMIN_PASSWORD", "")},
    )
    if status != 200 or not data or "access_token" not in data:
        raise SystemExit("login failed - check ADMIN_USERNAME/ADMIN_PASSWORD in .env")
    return data["access_token"]


def ensure_camera(token: str, sampling_rate: int) -> int:
    status, cams = req("/cameras", token)
    for c in cams or []:
        if c.get("name") == PERF_NAME:
            print(f"reusing camera id={c['id']} (sampling_rate={c.get('sampling_rate')})")
            return int(c["id"])
    status, cam = req(
        "/cameras", token, method="POST",
        body={"name": PERF_NAME, "file_path": PERF_FILE, "type": "entry",
              "sampling_rate": sampling_rate},
    )
    if status not in (200, 201) or not cam:
        raise SystemExit(f"camera create failed: {status} {cam}")
    print(f"created camera id={cam['id']} file_path={PERF_FILE} "
          f"sampling_rate={sampling_rate}")
    return int(cam["id"])


def wait_running(token: str, cam_id: int, timeout_s: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            _, last = req(f"/cameras/{cam_id}/live-status", token)
        except (urllib.error.URLError, OSError) as exc:
            last = {"error": f"status fetch failed: {exc}"}
        if last.get("has_frame") and last.get("running"):
            return last
        time.sleep(1.0)
    return last


def _num(value) -> float | None:
    """live-status returns fps/infer_ms as strings; coerce safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def wait_steady(token: str, cam_id: int, timeout_s: float = 60.0) -> dict:
    """Wait for steady state (warm model, fresh frames) before sampling.

    The first RPC after a container (re)start pays model warm-up - that
    number is real but it is NOT the steady-state cost, so it is captured
    separately as cold_start.
    """
    deadline = time.monotonic() + timeout_s
    first: dict = {}
    while time.monotonic() < deadline:
        try:
            _, st = req(f"/cameras/{cam_id}/live-status", token)
        except (urllib.error.URLError, OSError) as exc:
            st = {"error": str(exc)}
        if not first and st.get("infer_ms"):
            first = st
        age = st.get("frame_age_s")
        fps = _num(st.get("fps"))
        if (st.get("has_frame") and isinstance(age, (int, float)) and age < 2.0
                and fps is not None and fps > 0.5):
            return first, st
        time.sleep(1.0)
    return first, st


def sample_status(token: str, cam_id: int, samples: int) -> list[dict]:
    rows: list[dict] = []
    for _ in range(samples):
        t0 = time.monotonic()
        try:
            _, st = req(f"/cameras/{cam_id}/live-status", token)
        except (urllib.error.URLError, OSError) as exc:
            st = {"error": str(exc)}
        rows.append({"t": round(time.monotonic() - t0, 3), **st})
        time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
    return rows


def measure_mjpeg(token: str, cam_id: int, window_s: float) -> dict:
    """Stream live.mjpeg for a bounded window; count parts + arrival times."""
    r = urllib.request.Request(BASE + f"/cameras/{cam_id}/live.mjpeg")
    r.add_header("Authorization", f"Bearer {token}")
    arrivals: list[float] = []
    ages: list[float] = []
    bytes_total = 0
    start = time.monotonic()
    with urllib.request.urlopen(r, timeout=window_s + 15) as resp:
        date_hdr = ""
        for k, v in resp.headers.items():
            if k.lower() == "date":
                date_hdr = v
        server_now = None
        try:
            import email.utils
            server_now = email.utils.parsedate_to_datetime(date_hdr).timestamp()
        except Exception:
            server_now = None
        skew = (server_now - time.time()) if server_now else 0.0
        buf = b""
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            buf += chunk
            bytes_total += len(chunk)
            while True:
                m = TS_RE.search(buf)
                if not m:
                    break
                ts = float(m.group(1))
                now = time.time() + skew
                if ts > 0:
                    ages.append(now - ts)
                arrivals.append(time.monotonic())
                # drop everything up to and including this part's header
                buf = buf[m.end():]
                idx = buf.find(b"\r\n")
                if idx >= 0:
                    buf = buf[idx + 2:]
            if time.monotonic() - start >= window_s:
                break
    dur = arrivals[-1] - arrivals[0] if len(arrivals) > 1 else 0.0
    gaps = [arrivals[i + 1] - arrivals[i] for i in range(len(arrivals) - 1)]
    return {
        "window_s": round(dur, 2),
        "parts": len(arrivals),
        "delivered_fps": round(len(arrivals) / dur, 2) if dur > 0 else 0.0,
        "mean_part_gap_ms": round(statistics.fmean(gaps) * 1000, 1) if gaps else None,
        "max_part_gap_ms": round(max(gaps) * 1000, 1) if gaps else None,
        "bytes_total": bytes_total,
        "x_frame_ts_age_s": {
            "mean": round(statistics.fmean(ages), 3) if ages else None,
            "min": round(min(ages), 3) if ages else None,
            "max": round(max(ages), 3) if ages else None,
        } if ages else None,
        "note": "X-Frame-Ts age uses the response Date header for skew "
                "(+/-1 s resolution); the authoritative age is live-status "
                "frame_age_s, computed on the server clock",
    }


def measure_snapshots(token: str, cam_id: int, count: int) -> dict:
    lat: list[float] = []
    sizes: list[int] = []
    for _ in range(count):
        t0 = time.perf_counter()
        r = urllib.request.Request(BASE + f"/cameras/{cam_id}/snapshot")
        r.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(r, timeout=15) as resp:
            body = resp.read()
        lat.append((time.perf_counter() - t0) * 1000.0)
        sizes.append(len(body))
        time.sleep(0.1)
    return {
        "count": count,
        "mean_ms": round(statistics.fmean(lat), 1),
        "p50_ms": round(statistics.median(lat), 1),
        "max_ms": round(max(lat), 1),
        "min_ms": round(min(lat), 1),
        "mean_jpeg_bytes": int(statistics.fmean(sizes)),
        "verdict": "frame-bus hit (no source open)"
                   if statistics.fmean(lat) < 60 else "SLOW - possible source probe",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--create", action="store_true", help="create the perf camera")
    ap.add_argument("--start", action="store_true", help="start the camera pipeline")
    ap.add_argument("--stop", action="store_true", help="stop the camera pipeline")
    ap.add_argument("--camera-id", type=int)
    ap.add_argument("--sampling-rate", type=int, default=5,
                    help="deployment default DEFAULT_SAMPLING_RATE=5")
    ap.add_argument("--samples", type=int, default=15, help="live-status samples @1s")
    ap.add_argument("--window", type=float, default=12.0, help="MJPEG window (s)")
    ap.add_argument("--snapshots", type=int, default=20)
    args = ap.parse_args()

    env = load_env()
    token = login(env)
    cam_id = args.camera_id
    if cam_id is None and args.create:
        cam_id = ensure_camera(token, args.sampling_rate)
    if cam_id is None:
        raise SystemExit("--camera-id or --create required")
    if args.stop:
        _, resp = req(f"/cameras/{cam_id}/stop", token, method="POST")
        print(json.dumps(resp, indent=2))
        return 0
    if args.start:
        _, resp = req(f"/cameras/{cam_id}/start", token, method="POST")
        print("start:", json.dumps(resp))
    st = wait_running(token, cam_id)
    print("first live-status:", json.dumps(st))
    cold, steady = wait_steady(token, cam_id)
    print("steady-state reached:", json.dumps(steady))

    status_rows = sample_status(token, cam_id, args.samples)
    mjpeg = measure_mjpeg(token, cam_id, args.window)
    snap_before = measure_snapshots(token, cam_id, args.snapshots)
    _, st_after = req(f"/cameras/{cam_id}/live-status", token)

    fps_vals = [v for v in (_num(r.get("fps")) for r in status_rows) if v is not None]
    age_vals = [v for v in (_num(r.get("frame_age_s")) for r in status_rows) if v is not None]
    infer_vals = [v for v in (_num(r.get("infer_ms")) for r in status_rows) if v is not None]

    summary = {
        "camera_id": cam_id,
        "cold_start_first_rpc": {
            "infer_ms": _num(cold.get("infer_ms")),
            "frame_age_s": _num(cold.get("frame_age_s")),
            "note": "first RPC after container (re)start pays model warm-up",
        },
        "live_status_telemetry": {
            "samples": len(status_rows),
            "fps_publish_mean": round(statistics.fmean(fps_vals), 2) if fps_vals else None,
            "fps_publish_min": min(fps_vals) if fps_vals else None,
            "fps_publish_max": max(fps_vals) if fps_vals else None,
            "infer_ms_mean": round(statistics.fmean(infer_vals), 1) if infer_vals else None,
            "infer_ms_max": round(max(infer_vals), 1) if infer_vals else None,
            "frame_age_s_mean": round(statistics.fmean(age_vals), 2) if age_vals else None,
            "frame_age_s_max": round(max(age_vals), 2) if age_vals else None,
            "faces_seen": sorted({r.get("faces") for r in status_rows}),
            "infer_ok_all": all(r.get("infer_ok") for r in status_rows),
        },
        "mjpeg_delivery": mjpeg,
        "snapshot_burst": snap_before,
        "telemetry_after_burst": {
            "fps": st_after.get("fps"),
            "frame_age_s": st_after.get("frame_age_s"),
            "infer_ms": st_after.get("infer_ms"),
            "has_frame": st_after.get("has_frame"),
        },
    }
    print("\n=== BASELINE SUMMARY ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
