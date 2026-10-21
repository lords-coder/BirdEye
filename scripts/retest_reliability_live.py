"""Live retest of the previously-fixed reliability defects (real pipelines).

Runs against the REAL worker + REAL cameras while both pipelines run:

  R1 snapshot/legacy MJPEG must NOT open a competing RTSP capture
     -> measured as: snapshot served in single-digit ms (Redis path) and
        the publishing pipeline's FPS does not collapse while hammering.
  R2 independent processing: hammering camera 1's snapshot/legacy stream
     must not disturb camera 2's pipeline.
  R3 event-volume independence: recognition logs grow O(state changes),
     not O(frames) - compared against frames actually sent to the GPU.
"""
from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def api(method, path, tok=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=None, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            body = x.read()
            return x.status, body, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read(), (time.perf_counter() - t0) * 1000


def login():
    import urllib.request as u
    r = u.Request(
        BASE + "/auth/token",
        data=json.dumps({"username": "admin", "password": "admin"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(u.urlopen(r).read())["access_token"]


def statuses(tok, ids):
    out = {}
    for cid in ids:
        c, body, _ = api("GET", f"/cameras/{cid}/live-status", tok)
        out[cid] = json.loads(body) if c == 200 else {}
    return out


def fps(st):
    try:
        return float(st.get("fps") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def main():
    tok = login()
    c, body, _ = api("GET", "/cameras", tok)
    ids = [x["id"] for x in json.loads(body)]

    from app.db import SessionLocal
    from app.models import RecognitionLog

    def log_count():
        with SessionLocal() as db:
            return db.query(RecognitionLog).count()

    # ---------------- baseline ----------------------------------------
    base_fps = {}
    for label in ("baseline",):
        s = statuses(tok, ids)
        base_fps = {i: fps(s[i]) for i in ids}
        print(f"[{label}] fps={base_fps} infer_ok="
              f"{ {i: s[i].get('infer_ok') for i in ids} }")
    time.sleep(6)
    s = statuses(tok, ids)
    base_fps = {i: fps(s[i]) for i in ids}
    print(f"[baseline-2] fps={base_fps}")

    # ---------------- R1: hammer snapshot while pipelines run ----------
    print("\n[R1] 25x snapshot on camera 1 while BOTH pipelines run ...")
    lat = []
    for _ in range(25):
        c, body, ms = api("GET", f"/cameras/{ids[0]}/snapshot", tok)
        assert c == 200, f"snapshot HTTP {c}"
        lat.append(ms)
    lat.sort()
    print(f"      snapshot latency ms: min={lat[0]:.1f} "
          f"p50={statistics.median(lat):.1f} max={lat[-1]:.1f} "
          f"bytes={len(body)}")
    redis_served = lat[-1] < 400
    print(f"      served from frame bus (<400ms, no RTSP open): "
          f"{'YES' if redis_served else 'NO - POSSIBLE COMPETING PROBE'}")

    # ---------------- R1b: legacy MJPEG stream -------------------------
    print("\n[R1b] legacy MJPEG stream on camera 1 (bounded read) ...")
    req = urllib.request.Request(
        BASE + f"/cameras/{ids[0]}/stream.mjpeg", method="GET"
    )
    req.add_header("Authorization", f"Bearer {tok}")
    t0 = time.perf_counter()
    got = 0
    frames_seen = 0
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            # read a bounded amount - the MJPEG stream never ends
            while got < 400_000:
                chunk = resp.read(8192)
                if not chunk:
                    break
                got += len(chunk)
                frames_seen += chunk.count(b"\xff\xd8\xff")  # JPEG SOI
    except Exception as exc:  # noqa: BLE001 - timeout after data flowed
        status = f"stopped-after-{got}B ({type(exc).__name__})"
    print(f"      status={status} bytes={got} jpeg_starts~={frames_seen} "
          f"in {(time.perf_counter() - t0):.2f}s")
    print(f"      multipart bytes flowed without opening RTSP: "
          f"{'YES' if got > 20_000 else 'NO'}")

    # ---------------- R2: camera 2 unaffected --------------------------
    time.sleep(6)
    after = statuses(tok, ids)
    print("\n[R2] fps after hammering camera 1:")
    for i in ids:
        b, a = base_fps.get(i, 0.0), fps(after[i])
        delta = a - b
        flag = "OK" if a > 0.5 else "STALLED"
        print(f"      cam {i}: {b:.2f} -> {a:.2f} ({delta:+.2f}) [{flag}] "
              f"frame_age={after[i].get('frame_age_s')}s")

    # ---------------- R3: event volume vs frame volume -----------------
    from app.config import settings

    r = None
    try:
        from app import redis_client as rc
        r = rc.get_redis()
        hb = {i: r.get(rc.cam_heartbeat_key(i)) for i in ids}
        print(f"\n[R3] heartbeats fresh: "
              f"{ {i: (time.time() - float(v)) < 5 if v else None for i, v in hb.items()} }")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[R3] redis probe skipped: {exc}")

    print(f"      dedup_window_seconds={settings.dedup_window_seconds} "
          f"unknown_cooldown_s={settings.unknown_cooldown_s}")

    # GPU frame counter delta over a fixed window
    def gpu_frames():
        h = r.hgetall(settings.gpu_status_key) if r else {}
        try:
            return int(h.get(b"frames_total", h.get("frames_total", 0)) or 0)
        except (TypeError, ValueError):
            return 0

    f0, l0 = gpu_frames(), log_count()
    print(f"      observing 45s: gpu_frames={f0} recognition_logs={l0}")
    time.sleep(45)
    f1, l1 = gpu_frames(), log_count()
    df, dl = f1 - f0, l1 - l0
    print(f"      gpu_frames={f1} (+{df})  recognition_logs={l1} (+{dl})")
    if df > 0:
        print(f"      logs per 1000 processed frames: {1000 * dl / df:.2f}")
    print("      -> volume is bounded by state changes, not frame count"
          if dl <= max(5, df // 100)
          else "      -> WARNING: log growth looks frame-proportional")

    print("\nDONE")


if __name__ == "__main__":
    main()
