#!/usr/bin/env python
"""Measure the pipeline's DISPLAY publish rate directly (bounded).

``live-status`` reports ``fps`` = inference throughput (frames per RPC-second),
not how often a display frame is written to the Redis frame bus.  This script
measures the publish rate itself: it samples the snapshot endpoint (which reads
the SAME ``cam:{id}:frame`` key live.mjpeg serves) far faster than the stream
pacing allows, hashes each JPEG, and counts how many distinct frames actually
appear per second.

The duplicate-read ratio also shows the MJPEG pacing headroom: reads that land
on an already-seen frame mean the delivery loop is the limiter, not the
pipeline.

Credentials come from the existing ``.env``; no camera credential is involved.

    .venv\\Scripts\\python.exe scripts\\profile_publish_rate.py --camera-id 1
"""
from __future__ import annotations

import argparse
import hashlib
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
TS_RE = re.compile(rb"X-Frame-Ts:\s*([0-9.eE+-]+)")


def load_env() -> dict[str, str]:
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


def measure_kinds(token: str, cam_id: int, window_s: float) -> dict:
    """Count raw vs annotated parts (and distinct frame timestamps) on the wire.

    The capture tap publishes faster than inference, so an annotated frame is
    only briefly the newest value in the slot.  This proves annotated frames
    still REACH the browser (the recognition overlay must not become dead
    code) and shows what fraction of delivered parts are fresh.
    """
    r = urllib.request.Request(BASE + f"/cameras/{cam_id}/live.mjpeg")
    r.add_header("Authorization", f"Bearer {token}")
    kinds = {"raw": 0, "annotated": 0}
    stamps: list[float] = []
    parts = 0
    start = time.monotonic()
    with urllib.request.urlopen(r, timeout=window_s + 15) as resp:
        buf = b""
        while time.monotonic() - start < window_s:
            chunk = resp.read1(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                m = TS_RE.search(buf)
                if not m:
                    break
                stamps.append(float(m.group(1)))
                head = buf[max(0, m.start() - 120):m.end() + 120]
                if b"X-Frame-Kind: annotated" in head:
                    kinds["annotated"] += 1
                else:
                    kinds["raw"] += 1
                parts += 1
                buf = buf[m.end():]
                idx = buf.find(b"\r\n")
                if idx >= 0:
                    buf = buf[idx + 2:]
    dur = time.monotonic() - start
    distinct = len(set(stamps))
    return {
        "window_s": round(dur, 2),
        "parts": parts,
        "raw_parts": kinds["raw"],
        "annotated_parts": kinds["annotated"],
        "annotated_share": round(kinds["annotated"] / parts, 3) if parts else None,
        "distinct_frame_ts": distinct,
        "distinct_fps": round(distinct / dur, 2) if dur else None,
        "duplicate_share": round(1 - distinct / parts, 3) if parts else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera-id", type=int, required=True)
    ap.add_argument("--duration", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=0.04)
    ap.add_argument("--kinds", action="store_true",
                    help="count raw vs annotated parts on the wire")
    args = ap.parse_args()

    env = load_env()
    _, data = req("/auth/token", method="POST",
                  body={"username": env.get("ADMIN_USERNAME", "admin"),
                        "password": env.get("ADMIN_PASSWORD", "")})
    token = data["access_token"]

    if args.kinds:
        print(json.dumps({
            "camera_id": args.camera_id,
            "mjpeg_kind_mix": measure_kinds(token, args.camera_id, args.duration),
        }, indent=2))
        return 0

    seen_order: list[str] = []
    last_hash = None
    lat: list[float] = []
    dupes = 0
    t_end = time.monotonic() + args.duration
    while time.monotonic() < t_end:
        t0 = time.perf_counter()
        r = urllib.request.Request(
            BASE + f"/cameras/{args.camera_id}/snapshot")
        r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                body = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            print(f"snapshot failed: {exc}")
            break
        lat.append((time.perf_counter() - t0) * 1000.0)
        h = hashlib.sha1(body).hexdigest()[:12]
        if h == last_hash:
            dupes += 1
        else:
            seen_order.append(h)
            last_hash = h
        time.sleep(max(0.0, args.interval - (time.perf_counter() - t0)))

    reads = len(lat)
    distinct = len(seen_order)
    _, st = req(f"/cameras/{args.camera_id}/live-status", token)
    print(json.dumps({
        "camera_id": args.camera_id,
        "window_s": round(args.duration, 2),
        "snapshot_reads": reads,
        "distinct_display_frames": distinct,
        "display_publish_fps": round(distinct / args.duration, 2),
        "duplicate_reads": dupes,
        "duplicate_ratio": round(dupes / reads, 3) if reads else None,
        "snapshot_latency_ms": {
            "mean": round(statistics.fmean(lat), 1) if lat else None,
            "max": round(max(lat), 1) if lat else None,
        },
        "live_status_now": {
            "fps_infer_throughput": st.get("fps"),
            "infer_ms": st.get("infer_ms"),
            "faces": st.get("faces"),
            "viewers": st.get("viewers"),
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
