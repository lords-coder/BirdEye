"""Redis helpers: sync clients, key naming, dedup, event fan-out.

All REST handlers are synchronous (FastAPI runs them in a threadpool) so they
use the sync client; only the WebSocket bridge uses ``redis.asyncio``.
"""

from __future__ import annotations

import json
import struct
import time
from functools import lru_cache
from typing import Any

import redis as redis_lib

from .config import settings

# ------------------------------------------------------------------ clients
@lru_cache
def get_redis() -> redis_lib.Redis:
    """Shared short-timeout client for API-side operations."""
    return redis_lib.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )


@lru_cache
def get_binary_redis() -> redis_lib.Redis:
    """Binary-safe client for published video frames.

    The shared clients use ``decode_responses=True`` (right for JSON event
    payloads, fatal for JPEG bytes), so frame bytes travel on this separate
    connection instead.
    """
    return redis_lib.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=5,
    )


@lru_cache
def get_rpc_redis() -> redis_lib.Redis:
    """Client used for inference RPC blocking waits (longer socket timeout)."""
    """Client used for inference RPC blocking waits (longer socket timeout)."""
    return redis_lib.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=settings.inference_request_timeout_s + 5,
    )


def ping(url: str | None = None) -> bool:
    try:
        if url:
            client = redis_lib.Redis.from_url(
                url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
        else:
            client = get_redis()
        return bool(client.ping())
    except Exception:  # noqa: BLE001 - health probe
        return False


# ---------------------------------------------------------------- key names
def cam_state_key(camera_id: int) -> str:
    return f"cam:{camera_id}:state"


def cam_stop_key(camera_id: int) -> str:
    return f"cam:{camera_id}:stop"


def cam_heartbeat_key(camera_id: int) -> str:
    return f"cam:{camera_id}:hb"


def cam_task_key(camera_id: int) -> str:
    return f"cam:{camera_id}:task"


def cam_startup_deadline_key(camera_id: int) -> str:
    """Wall-clock deadline by which the pipeline must reach RUNNING."""
    return f"cam:{camera_id}:startup_deadline"


def cam_error_key(camera_id: int) -> str:
    """Human-readable reason the last start/pipeline failed (short TTL)."""
    return f"cam:{camera_id}:error"


def cam_slot_key(slot: int) -> str:
    return f"slot:{slot}:camera"


def cam_frame_key(camera_id: int) -> str:
    """Latest annotated JPEG for the browser monitor (binary, short TTL)."""
    return f"cam:{camera_id}:frame"


def cam_live_key(camera_id: int) -> str:
    """Hash: ts, fps, infer state, faces, error, viewers (short TTL)."""
    return f"cam:{camera_id}:live"


# -------------------------------------------------- live frame blob layout
# One binary SET carries {kind, capture timestamp, JPEG} so that:
#   * the display tap can publish a fresh frame WITHOUT touching the
#     inference status hash (which must keep reporting the pipeline's own
#     inference state), and
#   * the MJPEG endpoint can report each frame's OWN timestamp, so the
#     browser can tell a genuinely new frame from a repeated one.
# Prefix-less blobs (written by older builds) are still accepted and
# treated as raw frames with an unknown ts.
_FRAME_PREFIX_RAW = b"\x00"
_FRAME_PREFIX_ANNOTATED = b"\x01"
_FRAME_TS_LEN = 8  # little-endian double
_FRAME_JPEG_SOI = b"\xff\xd8"


def encode_live_frame(jpeg: bytes, *, frame_ts: float, annotated: bool) -> bytes:
    """Pack a display frame into the binary slot value (single SET, atomic)."""
    prefix = _FRAME_PREFIX_ANNOTATED if annotated else _FRAME_PREFIX_RAW
    return prefix + struct.pack("<d", float(frame_ts)) + jpeg


def decode_live_frame(blob) -> tuple[bytes, float | None, bool]:
    """Unpack a slot value -> ``(jpeg, frame_ts | None, annotated)``.

    A legacy prefix-less JPEG decodes as a raw frame with an unknown ts
    (callers fall back to the status hash in that case).
    """
    data = bytes(blob)
    header_len = 1 + _FRAME_TS_LEN
    if (
        len(data) > header_len + 2
        and data[:1] in (_FRAME_PREFIX_RAW, _FRAME_PREFIX_ANNOTATED)
        and data[header_len:header_len + 2] == _FRAME_JPEG_SOI
    ):
        ts = struct.unpack("<d", data[1:header_len])[0]
        return data[header_len:], ts, data[:1] == _FRAME_PREFIX_ANNOTATED
    return data, None, False


def publish_live_frame_raw(
    r_bin,
    camera_id: int,
    jpeg: bytes,
    *,
    frame_ts: float,
    ttl_s: int,
) -> None:
    """Display-tap publish: ONLY the binary frame slot (kind=raw).

    The inference status hash is deliberately left untouched so the monitor
    keeps showing the pipeline's own inference state (fps / infer_ms /
    faces / error) while fresher, un-annotated frames flow to the browser.
    Failures are swallowed: display must never break capture.
    """
    if not jpeg:
        return
    try:
        r_bin.set(
            cam_frame_key(camera_id),
            encode_live_frame(jpeg, frame_ts=frame_ts, annotated=False),
            ex=ttl_s,
        )
    except redis_lib.RedisError:
        pass


def publish_live_frame(
    r_bin,
    r,
    camera_id: int,
    jpeg: bytes,
    *,
    frame_ts: float,
    fps: float,
    infer_ok: bool,
    infer_ms: float | None,
    faces: int,
    error: str | None,
    ttl_s: int,
) -> None:
    """Share ONE latest frame for all viewers (no per-viewer capture).

    Pass an empty ``jpeg`` to update only the status hash (e.g. inference
    outage: the last good frame keeps ageing out on its own TTL while the
    hash carries the failure).
    """
    if jpeg:
        r_bin.set(
            cam_frame_key(camera_id),
            encode_live_frame(jpeg, frame_ts=frame_ts, annotated=True),
            ex=ttl_s,
        )
    try:
        r.hset(
            cam_live_key(camera_id),
            mapping={
                "ts": repr(frame_ts),
                "fps": repr(round(float(fps), 2)),
                "infer_ok": "1" if infer_ok else "0",
                "infer_ms": repr(round(float(infer_ms), 1)) if infer_ms is not None else "",
                "faces": str(int(faces)),
                "error": (error or "")[:200],
            },
        )
        r.expire(cam_live_key(camera_id), ttl_s)
    except redis_lib.RedisError:
        pass


def read_live_frame(r_bin, camera_id: int) -> bytes | None:
    """Latest published JPEG, or None when no fresh frame exists (key TTLs)."""
    try:
        blob = r_bin.get(cam_frame_key(camera_id))
    except redis_lib.RedisError:
        return None
    if blob is None:
        return None
    jpeg, _ts, _annotated = decode_live_frame(blob)
    return jpeg


def read_live_frame_ex(
    r_bin, camera_id: int
) -> tuple[bytes, float | None, bool] | None:
    """Latest published frame as ``(jpeg, frame_ts | None, annotated)``.

    ``frame_ts`` is the timestamp the frame was captured/published with, so
    streamers can distinguish a new frame from a repeated one; it is None
    only for legacy prefix-less values (callers then fall back to the status
    hash).
    """
    try:
        blob = r_bin.get(cam_frame_key(camera_id))
    except redis_lib.RedisError:
        return None
    if blob is None:
        return None
    return decode_live_frame(blob)


def read_live_meta(r, camera_id: int) -> dict:
    try:
        raw = r.hgetall(cam_live_key(camera_id))
    except redis_lib.RedisError:
        return {}
    return dict(raw or {})


def dedup_key(camera_id: int, part: str) -> str:
    return f"dedup:cam:{camera_id}:{part}"


# ------------------------------------------------------------- camera state
# Transient states a camera pipeline can occupy.  Each is only trustworthy
# while its heartbeat is fresh: the pipeline refreshes the heartbeat every
# batch and the API sets it at dispatch time, so an expired heartbeat means no
# worker can still own the camera and the stored key is a leftover.
CAMERA_TRANSIENT_STATES = ("starting", "running", "stopping", "unhealthy", "failed")


def get_camera_runtime(
    r: redis_lib.Redis | None = None, camera_id: int | None = None
) -> dict[str, Any]:
    """Read state/heartbeat/slot for a camera (or all cameras when id is None).

    ``state`` is the EFFECTIVE state.  A transient state whose heartbeat has
    expired is reported as ``stopped`` with ``stale=True`` (except ``running``,
    which becomes ``unhealthy`` so a live-but-failing pipeline is still
    distinguishable), because the owning task is gone - worker crash, worker or
    API/container restart, broker loss - and the stored key only survives for
    ``camera_state_ttl_s``.  ``stored_state`` keeps the raw value so callers can
    report the difference instead of treating a leftover as a fact.
    """
    r = r or get_redis()
    out: dict[str, Any] = {
        "state": "stopped",
        "stored_state": None,
        "running": False,
        "stale": False,
        "slot": None,
        "task_id": None,
        "last_heartbeat_at": None,
        "startup_deadline_at": None,
        "error": None,
    }
    if camera_id is None:
        return out
    try:
        state = r.get(cam_state_key(camera_id))
        task_id = r.get(cam_task_key(camera_id))
        hb = r.get(cam_heartbeat_key(camera_id))
        deadline = r.get(cam_startup_deadline_key(camera_id))
        err = r.get(cam_error_key(camera_id))
    except redis_lib.RedisError:
        return out
    if state:
        out["state"] = state
        out["stored_state"] = state
    if task_id:
        out["task_id"] = task_id
    hb_at: float | None = None
    if hb is not None:
        try:
            hb_at = float(hb)
        except (TypeError, ValueError):
            hb_at = None
        out["last_heartbeat_at"] = hb_at
    if deadline is not None:
        try:
            out["startup_deadline_at"] = float(deadline)
        except (TypeError, ValueError):
            pass
    if err is not None:
        out["error"] = err
    fresh = hb_at is not None and (time.time() - hb_at) <= settings.heartbeat_ttl_s

    if state == "running":
        out["running"] = bool(fresh)
        # Task claims to run but heartbeats expired -> mark unhealthy.
        if not fresh:
            out["state"] = "unhealthy"
    elif state in ("starting", "stopping", "failed") and not fresh:
        # No live task can own this camera.  Reporting the leftover verbatim is
        # what used to wedge a camera in STARTING for the entire 24 h state TTL.
        out["state"] = "stopped"
        out["stale"] = True

    out["slot"] = get_camera_slot(r, camera_id)
    return out


def clear_stale_camera_state(
    r: redis_lib.Redis,
    camera_id: int,
    runtime: dict[str, Any] | None = None,
) -> bool:
    """Drop leftover runtime keys for a camera no live task can own.

    Refuses to do anything while the heartbeat is fresh, so state can never be
    pulled out from under a working pipeline - this is the "verify no
    legitimate camera task is still running" guard.  Also releases the slot
    when this camera still owns it.  Returns True when something was removed.
    """
    runtime = runtime if runtime is not None else get_camera_runtime(r, camera_id)
    hb_at = runtime.get("last_heartbeat_at")
    if hb_at is not None and (time.time() - hb_at) <= settings.heartbeat_ttl_s:
        return False
    slot = runtime.get("slot")
    try:
        with r.pipeline() as pipe:
            for key in (
                cam_state_key(camera_id),
                cam_stop_key(camera_id),
                cam_heartbeat_key(camera_id),
                cam_task_key(camera_id),
            ):
                pipe.delete(key)
            pipe.srem(settings.running_cameras_key, camera_id)
            if slot is not None:
                owner = r.get(cam_slot_key(slot))
                if owner is not None and int(owner) == camera_id:
                    pipe.delete(cam_slot_key(slot))
            results = pipe.execute()
    except (redis_lib.RedisError, ValueError):
        return False
    return any(bool(x) for x in results)


def get_camera_slot(r: redis_lib.Redis, camera_id: int) -> int | None:
    try:
        for i in range(settings.camera_slots):
            owner = r.get(cam_slot_key(i))
            if owner is not None and int(owner) == camera_id:
                return i
    except (redis_lib.RedisError, ValueError):
        return None
    return None


def heartbeat_ok(r: redis_lib.Redis, camera_id: int) -> bool:
    try:
        return bool(r.exists(cam_heartbeat_key(camera_id)))
    except redis_lib.RedisError:
        return False


def touch_heartbeat(r: redis_lib.Redis, camera_id: int) -> None:
    r.set(cam_heartbeat_key(camera_id), str(time.time()), ex=settings.heartbeat_ttl_s)


# ------------------------------------------------------------------- dedup
def dedup_pass(
    r: redis_lib.Redis, camera_id: int, part: str,
    window: int | None = None,
) -> bool:
    """Return True exactly once per (camera, subject) within the window."""
    window = window if window is not None else settings.dedup_window_seconds
    if window <= 0:
        return True
    try:
        return bool(
            r.set(dedup_key(camera_id, part), str(time.time()), nx=True, ex=window)
        )
    except redis_lib.RedisError:
        # Redis down must not lose attendance data - treat as "pass".
        return True


# ------------------------------------------------------------- event fanout
def publish_event(r: redis_lib.Redis | None, payload: dict[str, Any]) -> None:
    """Publish to the WebSocket channel and keep a small replay backlog."""
    r = r or get_redis()
    data = json.dumps(payload, default=str, separators=(",", ":"))
    pipe = r.pipeline()
    pipe.publish(settings.events_channel, data)
    pipe.lpush(settings.events_recent_key, data)
    pipe.ltrim(settings.events_recent_key, 0, settings.events_recent_limit - 1)
    pipe.execute()


# -------------------------------------------------------- embedding version
def students_version(r: redis_lib.Redis | None = None) -> str | None:
    r = r or get_redis()
    try:
        return r.get(settings.students_version_key)
    except redis_lib.RedisError:
        return None


def bump_students_version(r: redis_lib.Redis | None = None) -> None:
    r = r or get_redis()
    try:
        r.incr(settings.students_version_key)
    except redis_lib.RedisError:
        pass
