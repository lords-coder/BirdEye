#!/usr/bin/env python
"""Inference canary: prove the REAL model works through the production path.

Downloads two public-domain sample faces (Obama / two people, from the
`face_recognition` examples repo) into ``$TEMP/opencode/canary`` and runs
them through ``app.inference.client`` exactly as the API and pipeline do
(``LocalInference().install()`` for single-process runs, or the shared
Redis service when ``--rpc`` is given).

    python scripts/validate_inference.py           # in-process engine
    python scripts/validate_inference.py --rpc     # via running inference svc

Exit codes: 0 = detection + embedding sane, 1 = failure.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLES = {
    "obama.jpg": "https://raw.githubusercontent.com/ageitgey/face_recognition"
                 "/master/examples/obama.jpg",
    "two_people.jpg": "https://raw.githubusercontent.com/ageitgey/face_recognition"
                      "/master/examples/two_people.jpg",
}


def _fetch(name: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    if not path.exists():
        urllib.request.urlretrieve(SAMPLES[name], path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rpc", action="store_true",
                    help="use the shared Redis inference service instead of "
                         "installing the engine in-process")
    args = ap.parse_args()

    asset_dir = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "canary"

    if not args.rpc:
        from app.inference.local import LocalInference

        LocalInference().install()
        print("[canary] in-process engine installed into app.inference.client")

    from app.inference import client as ic

    failures: list[str] = []

    # ---------------------------------------------------------- embedding
    img = _fetch("obama.jpg", asset_dir).read_bytes()
    t0 = time.time()
    try:
        res = ic.embed_images([img])
        vec = res[0]["embedding"]
        print(f"[canary] embed: dim={len(vec)} "
              f"({time.time() - t0:.2f}s)")
        if len(vec) != 512:
            failures.append(f"expected 512-d embedding, got {len(vec)}")
        norm = sum(x * x for x in vec) ** 0.5
        if not 0.99 <= norm <= 1.01:
            failures.append(f"embedding not L2-normalised (norm={norm:.4f})")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"embed_images failed: {exc}")

    # --------------------------------------------------------- detection
    t0 = time.time()
    try:
        det = ic.infer_frames([img])
        faces = det["results"][0]
        print(f"[canary] detect single face: {len(faces)} face(s), "
              f"scores={[round(f['score'], 3) for f in faces]} "
              f"({time.time() - t0:.2f}s)")
        if len(faces) != 1:
            failures.append(f"expected exactly 1 face in obama.jpg, got {len(faces)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"infer_frames(single) failed: {exc}")

    two = _fetch("two_people.jpg", asset_dir).read_bytes()
    t0 = time.time()
    try:
        det2 = ic.infer_frames([two])
        n = len(det2["results"][0])
        print(f"[canary] detect two people: {n} face(s) ({time.time() - t0:.2f}s)")
        if n != 2:
            failures.append(f"expected 2 faces in two_people.jpg, got {n}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"infer_frames(two) failed: {exc}")

    # ---------------------------------------------------------- providers
    import onnxruntime as ort

    print(f"[canary] onnxruntime available providers: {ort.get_available_providers()}")
    if not args.rpc:
        # The ACTIVE session providers are what matters: ORT keeps listing
        # CUDA even when its DLLs failed to load (engine warns then).
        from app.inference.local import _SHARED

        engine = _SHARED.get("engine")
        active = engine._active_providers() if engine is not None else None
        print(f"[canary] ACTIVE session providers: {active}")
        if active and active[0] == "CUDAExecutionProvider":
            print("[canary] GPU inference: ACTIVE (CUDA session)")
        elif active:
            print(f"[canary] GPU inference: NOT active (running {active[0]})")

    if failures:
        for f in failures:
            print(f"[canary] FAIL: {f}", file=sys.stderr)
        return 1
    print("[canary] OK - real model detects and embeds correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
