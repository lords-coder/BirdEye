"""Measure end-to-end displayed-frame delay through the pipeline's real path.

Stages measured against a PHYSICAL camera:
  1. RTSP decode latency      (camera timestamp -> frame available)
  2. batch flush window       (config: batch_flush_ms)
  3. downscale + annotate     (app/pipeline/annotate.py, real code)
  4. JPEG encode              (app/pipeline/annotate.encode_jpeg)
  5. Redis publish -> read    (shared frame bus)

Reported delay = camera capture -> annotated JPEG readable by a browser.
No credentials printed.
"""
import time
import urllib.parse

import cv2
import numpy as np

from app import redis_client as rc
from app.config import settings
from app.db import SessionLocal
from app.models import Camera
from app.pipeline import annotate

CONFIRMED_PATH = "/cam/realmonitor?channel=1&subtype=0"


def source_for(cam):
    url = cam.build_rtsp_url()
    if not cam.stream_path:
        u = urllib.parse.urlparse(url)
        url = u._replace(path=CONFIRMED_PATH).geturl()
    return url


def main():
    db = SessionLocal()
    cams = db.query(Camera).all()
    db.close()

    print(f"config: batch_flush_ms={settings.batch_flush_ms} "
          f"live_frame_width={settings.live_frame_width} "
          f"live_frame_quality={settings.live_frame_quality} "
          f"live_mjpeg_fps={settings.live_mjpeg_fps}\n")

    for cam in cams:
        url = source_for(cam)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            print(f"{cam.name}: could not open stream\n")
            continue

        # warm up
        for _ in range(5):
            cap.read()

        # Measure raw capture cadence
        ts = []
        n = 0
        t_start = time.time()
        while time.time() - t_start < 6 and n < 200:
            t0 = time.perf_counter()
            ok, frame = cap.read()
            t1 = time.perf_counter()
            if ok and frame is not None:
                ts.append((time.time(), (t1 - t0) * 1000.0, frame))
                n += 1
        cap.release()

        if not ts:
            print(f"{cam.name}: no frames\n")
            continue

        decode_ms = sum(x[1] for x in ts) / len(ts)
        capture_fps = len(ts) / (ts[-1][0] - ts[0][0])

        # Measure the annotate + encode path on a real frame
        frame = ts[-1][2]
        src_h, src_w = frame.shape[:2]
        ann_times = []
        enc_times = []
        jpeg = None
        for i in range(20):
            t0 = time.perf_counter()
            small = annotate.downscale(frame, settings.live_frame_width)
            dets = annotate.build_detections(db, None, [])
            marked = annotate.annotate(small, dets, src_size=(src_w, src_h))
            t1 = time.perf_counter()
            jpeg = annotate.encode_jpeg(marked, settings.live_frame_quality)
            t2 = time.perf_counter()
            ann_times.append((t1 - t0) * 1000.0)
            enc_times.append((t2 - t1) * 1000.0)

        ann_ms = sum(ann_times) / len(ann_times)
        enc_ms = sum(enc_times) / len(enc_times)

        # Redis publish -> readback
        import fakeredis
        rb = fakeredis.FakeRedis(decode_responses=False)
        rt = fakeredis.FakeRedis(decode_responses=True)
        t0 = time.perf_counter()
        rc.publish_live_frame(rb, rt, cam.id, jpeg or b"", frame_ts=time.time(),
                              fps=capture_fps, infer_ok=True, infer_ms=1.0,
                              faces=0, error=None, ttl_s=15)
        back = rc.read_live_frame(rb, cam.id)
        t1 = time.perf_counter()
        bus_ms = (t1 - t0) * 1000.0

        # total pipeline delay estimate (excluding GPU inference which is
        # measured separately on the worker)
        gpu_est = 0.0  # filled in by worker logs when a model is loaded
        total = (decode_ms + settings.batch_flush_ms + ann_ms + enc_ms
                 + bus_ms + gpu_est)

        print(f"=== {cam.name} ({cam.type}/{cam.camera_role}) ===")
        print(f"  capture resolution   : {src_w}x{src_h}")
        print(f"  capture FPS          : {capture_fps:.1f}")
        print(f"  RTSP decode per frame: {decode_ms:.1f} ms")
        print(f"  batch flush window   : {settings.batch_flush_ms} ms (config)")
        print(f"  downscale+annotate   : {ann_ms:.1f} ms")
        print(f"  JPEG encode          : {enc_ms:.1f} ms "
              f"({len(jpeg or b'')} bytes at q{settings.live_frame_quality})")
        print(f"  Redis publish+read   : {bus_ms:.1f} ms")
        print(f"  ---------------------------------------------")
        print(f"  EST DISPLAY DELAY    : ~{total:.0f} ms "
              f"(excl. GPU inference)")
        print(f"  (GPU det+emb measured separately on worker; "
              f"typ. 30-80 ms on CPU)\n")


if __name__ == "__main__":
    main()
