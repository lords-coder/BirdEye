#!/usr/bin/env python
"""Phase-1 baseline: bounded stage-level profiler for the frame path.

The physical Dahua cameras are unavailable in this deployment (their rows are
metadata-only: ``stream_path`` is NULL and no RTSP endpoint is reachable), so
physical-camera capture measurements are marked BLOCKED.  This script measures
the *stage* costs the pipeline pays per frame against a synthetic clip that
mimics the measured main-stream format (2560x1440 @ 26 FPS), so the bottleneck
can still be located with real numbers.

Stages measured (each per-frame, ``time.perf_counter()``, bounded sample):

  decode           cv2.VideoCapture.read()                       (capture)
  jpeg_q85_full    inference-path JPEG, full res, quality 85    (camera_task._encode_jpeg)
  downscale_640    display-path downscale to live_frame_width   (annotate.downscale)
  annotate         overlay drawing over the downscaled frame    (annotate.annotate)
  jpeg_q70_640     display-path JPEG, 640 px, quality 70        (annotate.encode_jpeg)

No network, no Redis, no GPU: this isolates CPU-side costs only.  GPU/RPC cost
is measured separately against the running inference service (report 24).

    .venv\\Scripts\\python.exe scripts\\profile_frame_path.py --make
    .venv\\Scripts\\python.exe scripts\\profile_frame_path.py --video <path>
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys
import time

import cv2
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = REPO / "samples" / "videos" / "perf_baseline.mp4"
PHOTO_DIR = REPO / "data" / "students" / "3"

# Matches the measured Dahua main stream (report 23: 2560x1440, 25.6-27.6 FPS).
WIDTH, HEIGHT, FPS, SECONDS = 2560, 1440, 26, 20


def make_video(out: pathlib.Path) -> pathlib.Path:
    """Synthetic main-stream clip: real face + moving scene + mild noise.

    Static content would compress to almost nothing and decode far cheaper
    than a real camera, so every frame carries a slowly moving element and
    low-amplitude noise.  Decode cost is therefore a *lower bound* on the
    real RTSP path (which also pays network + H.264 jitter).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    photos = sorted(PHOTO_DIR.glob("photo_*.jpg"))
    face = None
    if photos:
        img = cv2.imread(str(photos[0]))
        if img is not None:
            h, w = img.shape[:2]
            scale = 360.0 / max(h, w)
            face = cv2.resize(img, (int(w * scale), int(h * scale)))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, float(FPS), (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise SystemExit("VideoWriter could not be opened (mp4v)")

    total = int(FPS * SECONDS)
    rng = np.random.default_rng(7)
    for i in range(total):
        frame = np.full((HEIGHT, WIDTH, 3), 32, dtype=np.uint8)
        # moving bar so the encoder cannot collapse the clip to near-zero work
        x = int((i / total) * (WIDTH - 400))
        cv2.rectangle(frame, (x, 900), (x + 400, 1300), (70, 90, 120), -1)
        if face is not None:
            fh, fw = face.shape[:2]
            y0, x0 = 300, 1100
            frame[y0 : y0 + fh, x0 : x0 + fw] = face
        # low-amplitude noise keeps the bitrate (and decode work) realistic
        noise = rng.integers(-4, 5, size=(HEIGHT, WIDTH, 1), dtype=np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        writer.write(frame)
    writer.release()
    return out


def _ms(samples: list[float]) -> dict:
    return {
        "n": len(samples),
        "mean_ms": round(statistics.fmean(samples), 2),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(statistics.quantiles(samples, n=20)[18], 2)
        if len(samples) >= 20
        else round(max(samples), 2),
        "max_ms": round(max(samples), 2),
    }


def profile(video: pathlib.Path, frames: int) -> None:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    ok, first = cap.read()
    if not ok:
        raise SystemExit("video yielded no frames")
    h, w = first.shape[:2]

    sys.path.insert(0, str(REPO))
    from app.config import settings  # noqa: E402
    from app.pipeline import annotate as ann  # noqa: E402
    from app.pipeline.camera_task import _encode_jpeg  # noqa: E402

    decode: list[float] = []
    enc85: list[float] = []
    down: list[float] = []
    anno: list[float] = []
    enc70: list[float] = []
    bytes85: list[int] = []
    bytes70: list[int] = []

    packets = [first]
    while len(packets) < frames:
        ok, img = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the clip
            ok, img = cap.read()
            if not ok:
                break
        packets.append(img)
    cap.release()

    decode_pass = len(packets)
    t0 = time.perf_counter()
    for _ in packets:
        pass  # decode cost is measured by the read loop above
    _ = t0

    # Re-measure decode properly with its own timed loop.
    cap = cv2.VideoCapture(str(video))
    decode = []
    for _ in range(min(frames, 120)):
        t = time.perf_counter()
        ok, img = cap.read()
        decode.append((time.perf_counter() - t) * 1000.0)
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    cap.release()

    for img in packets[: len(decode)]:
        t = time.perf_counter()
        jb = _encode_jpeg(img)
        enc85.append((time.perf_counter() - t) * 1000.0)
        bytes85.append(len(jb or b""))

        t = time.perf_counter()
        small = ann.downscale(img, settings.live_frame_width)
        down.append((time.perf_counter() - t) * 1000.0)

        t = time.perf_counter()
        sh, sw = img.shape[:2]
        marked = ann.annotate(small, [], src_size=(sw, sh))
        anno.append((time.perf_counter() - t) * 1000.0)

        t = time.perf_counter()
        lb = ann.encode_jpeg(marked, settings.live_frame_quality)
        enc70.append((time.perf_counter() - t) * 1000.0)
        bytes70.append(len(lb or b""))

    print(f"video: {video}")
    print(f"  format: {w}x{h} @ {src_fps:.1f} FPS reported, sample={len(decode)} frames")
    print(f"  settings: jpeg_quality={settings.jpeg_quality} "
          f"live_frame_width={settings.live_frame_width} "
          f"live_frame_quality={settings.live_frame_quality} "
          f"sampling_rate(default)={settings.default_sampling_rate} "
          f"batch_size={settings.batch_size} batch_flush_ms={settings.batch_flush_ms} "
          f"live_mjpeg_fps={settings.live_mjpeg_fps}")
    print()
    print(f"  decode           {_ms(decode)}")
    print(f"  jpeg_q85_full    {_ms(enc85)}  mean_bytes={int(statistics.fmean(bytes85))}")
    print(f"  downscale_640    {_ms(down)}")
    print(f"  annotate         {_ms(anno)}")
    print(f"  jpeg_q70_640     {_ms(enc70)}  mean_bytes={int(statistics.fmean(bytes70))}")

    infer_path = statistics.fmean(decode) + statistics.fmean(enc85)
    disp_path = statistics.fmean(down) + statistics.fmean(anno) + statistics.fmean(enc70)
    print()
    print(f"  CPU cost/frame  inference path (decode+jpeg_q85_full): {infer_path:.2f} ms")
    print(f"  CPU cost/frame  display  path (downscale+annotate+q70): {disp_path:.2f} ms")
    print(f"  => decode ceiling if every frame were inferred: {1000.0/infer_path:.1f} FPS")
    print(f"  => with sampling_rate={settings.default_sampling_rate}: "
          f"{1000.0/(infer_path*settings.default_sampling_rate):.1f} FPS sampled")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=pathlib.Path, default=DEFAULT_VIDEO)
    ap.add_argument("--make", action="store_true", help="generate the clip first")
    ap.add_argument("--frames", type=int, default=120, help="bounded sample size")
    args = ap.parse_args()
    if args.make or not args.video.exists():
        print(f"generating {args.video} ...")
        make_video(args.video)
    profile(args.video, args.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
