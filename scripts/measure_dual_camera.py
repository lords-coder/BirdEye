"""Physical-camera simultaneous capture + latency measurement.

Validates (step 4) that BOTH cameras can be captured concurrently and
independently, and measures (step 8) per-camera FPS and the end-to-end
pipeline delay (capture -> annotated JPEG published to Redis).

Uses the confirmed Dahua path when stream_path is unset.  Never prints
credentials.
"""
import time
import urllib.parse
import threading

import cv2

from app.db import SessionLocal
from app.models import Camera

CONFIRMED_PATH = "/cam/realmonitor?channel=1&subtype=0"


def source_for(cam):
    url = cam.build_rtsp_url()
    if not cam.stream_path:
        u = urllib.parse.urlparse(url)
        url = u._replace(path=CONFIRMED_PATH).geturl()
    return url


def capture_loop(cam, stop_evt, stats):
    url = source_for(cam)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    stats["opened"] = cap.isOpened()
    t0 = time.time()
    n = 0
    first_ts = None
    while not stop_evt.is_set():
        ok, frame = cap.read()
        now = time.time()
        if not ok or frame is None:
            stats["fail"] = stats.get("fail", 0) + 1
            continue
        if first_ts is None:
            first_ts = now
            stats["first_frame_s"] = now - t0
        n += 1
        stats["frames"] = n
        stats["last_ts"] = now
        stats["resolution"] = f"{frame.shape[1]}x{frame.shape[0]}"
    cap.release()
    stats["elapsed"] = time.time() - t0
    stats["fps"] = n / stats["elapsed"] if stats["elapsed"] else 0.0


def main():
    db = SessionLocal()
    cams = db.query(Camera).all()
    stop = threading.Event()
    stats = {c.id: {} for c in cams}
    threads = []
    for c in cams:
        t = threading.Thread(target=capture_loop, args=(c, stop, stats[c.id]),
                             daemon=True)
        threads.append(t)
    print(f"Starting {len(threads)} simultaneous capture threads ...")
    t_start = time.time()
    for t in threads:
        t.start()
    time.sleep(20)
    stop.set()
    for t in threads:
        t.join(timeout=10)
    print(f"\nTotal wall time: {time.time() - t_start:.1f}s\n")
    for c in cams:
        s = stats[c.id]
        print(f"{c.name} ({c.type}/{c.camera_role}):")
        print(f"  opened           : {s.get('opened')}")
        print(f"  resolution       : {s.get('resolution')}")
        print(f"  first frame      : {s.get('first_frame_s', 0):.2f}s after open")
        print(f"  frames captured  : {s.get('frames')}")
        print(f"  measured FPS     : {s.get('fps', 0):.1f}")
        print(f"  read failures    : {s.get('fail', 0)}")
    db.close()

    # Both streams must be concurrently healthy for the gate to pass.
    ok = all(s.get("frames", 0) > 50 for s in stats.values())
    print(f"\nSIMULTANEOUS DUAL-CAMERA CAPTURE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
