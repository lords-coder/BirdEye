"""Camera registration, secure configuration, connection testing + control.

Secrets: a camera's stream password is accepted on create/update, Fernet-
encrypted at rest (``app.crypto.encrypt_text``) and NEVER returned by any
response - serializers expose ``has_password`` and a credential-redacted
``display_url`` instead.  Legacy rows that stored an inline-credential
``rtsp_url`` have the userinfo extracted into the structured fields on their
next update; until then every response redacts it.

Slot model: the worker exposes ``CAMERA_SLOTS`` dedicated Celery queues
(``camera_slot_0 .. N-1``).  Starting a camera claims a free slot (Redis
NX + stale-heartbeat reclaim), enqueues the long-running pipeline task onto
that queue, and records state keys.  Stopping sets a stop flag that the
pipeline checks every few hundred milliseconds; ``force=true`` additionally
revokes the Celery task and clears state immediately.  Disabled cameras
cannot be started (their configuration is kept for later reactivation).
"""

from __future__ import annotations

import logging
import time as time_mod

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import redis_client as rc
from ..config import settings
from ..crypto import encrypt_text
from ..models import Camera
from ..schemas import (
    CameraCreate,
    CameraOut,
    CameraRuntime,
    CameraTestRequest,
    CameraTestResult,
    CameraUpdate,
    OkResponse,
    StartStopResponse,
    redact_rtsp_url,
)
from ..security import resolve_principal
from ..services import camera_test
from ..services.events import camera_state_event, publish
from ..timeutil import utcnow
from ..workers.celery_app import celery_app
from .deps import get_current_admin, get_db

logger = logging.getLogger("app.api.cameras")

router = APIRouter(prefix="/cameras", tags=["cameras"])

_bearer = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------- helpers
def _camera_out(camera: Camera) -> CameraOut:
    rt = rc.get_camera_runtime(camera_id=camera.id)
    runtime = CameraRuntime(
        state=rt["state"],  # type: ignore[arg-type]
        running=rt["running"],
        slot=rt["slot"],
        task_id=rt["task_id"],
        last_heartbeat_at=rt["last_heartbeat_at"],
    )
    # Never echo a credentialed URL: legacy rtsp_url rows are redacted, and
    # the structured URL is composed WITHOUT credentials for display.
    if camera.host:
        display = f"rtsp://{camera.host}:{camera.port or 554}{camera.stream_path or ''}"
    elif camera.rtsp_url:
        display = camera.rtsp_url
    else:
        display = camera.file_path
    return CameraOut(
        id=camera.id,
        name=camera.name,
        location=camera.location,
        rtsp_url=redact_rtsp_url(camera.rtsp_url),
        file_path=camera.file_path,
        type=camera.type,  # type: ignore[arg-type]
        sampling_rate=camera.sampling_rate,
        host=camera.host,
        port=camera.port,
        username=camera.username,
        stream_path=camera.stream_path,
        camera_role=camera.camera_role,  # type: ignore[arg-type]
        room=camera.room,
        notes=camera.notes,
        enabled=camera.enabled,
        has_password=bool(camera.password_encrypted),
        display_url=redact_rtsp_url(display),
        last_frame_at=camera.last_frame_at,
        last_test_status=camera.last_test_status,
        last_tested_at=camera.last_tested_at,
        last_error=camera.last_error,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
        runtime=runtime,
    )


def _get_or_404(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
    return camera


def _redis_or_503() -> redis_lib.Redis:
    try:
        r = rc.get_redis()
        r.ping()
        return r
    except redis_lib.RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"redis unavailable: {exc}",
        ) from exc


def _celery_or_503():
    return celery_app  # imported at module load; connection happens on send


def _acquire_slot(r: redis_lib.Redis, camera_id: int) -> int | None:
    for i in range(settings.camera_slots):
        key = rc.cam_slot_key(i)
        owner = r.get(key)
        if owner is not None and int(owner) == camera_id:
            return i
        if owner is not None:
            if rc.heartbeat_ok(r, int(owner)):
                continue  # live owner occupies this slot
            r.delete(key)  # stale owner (worker died) -> reclaim
        if r.set(key, camera_id, nx=True):
            return i
    return None


def _extract_inline_credentials(camera: Camera) -> None:
    """Move ``user:pass@`` out of a legacy rtsp_url into structured fields.

    Keeps the pipeline source identical while removing the credential from
    the column that used to be echoed to browsers.  Runs once per update of
    such a row; idempotent afterwards.
    """
    url = camera.rtsp_url
    if not url or "@" not in url.split("://", 1)[-1].split("/", 1)[0]:
        return
    try:
        from urllib.parse import unquote, urlsplit

        parts = urlsplit(url)
        if parts.username is None:
            return
        rest = parts.path or ""
        if parts.query:
            rest += "?" + parts.query
        camera.username = camera.username or unquote(parts.username)
        if parts.password and not camera.password_encrypted:
            camera.password_encrypted = encrypt_text(unquote(parts.password))
        camera.host = camera.host or (parts.hostname or None)
        camera.port = camera.port or (parts.port or None)
        camera.stream_path = camera.stream_path or (rest or None)
        camera.rtsp_url = None  # structured now; nothing to echo back
        logger.info(
            "camera %s: inline credentials migrated to encrypted structured fields",
            camera.id,
        )
    except Exception:  # noqa: BLE001 - never fail an update on migration
        logger.warning("camera %s: could not migrate inline credentials", camera.id)


def _apply_create_fields(camera: Camera, body: CameraCreate) -> None:
    camera.name = body.name.strip()
    camera.location = body.location
    camera.rtsp_url = body.rtsp_url
    camera.file_path = body.file_path
    camera.type = body.type
    camera.sampling_rate = body.sampling_rate
    camera.host = body.host
    camera.port = body.port
    camera.username = body.username
    camera.stream_path = body.stream_path
    camera.camera_role = body.camera_role
    camera.room = body.room
    camera.notes = body.notes
    camera.enabled = body.enabled
    if body.password:
        camera.password_encrypted = encrypt_text(body.password)


def _apply_update_fields(camera: Camera, body: CameraUpdate, db: Session) -> None:
    fields = body.model_dump(
        exclude_unset=True, exclude={"password", "clear_password"}
    )
    if "name" in fields and fields["name"] != camera.name:
        clash = db.scalars(
            select(Camera).where(Camera.name == fields["name"])
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="camera name already exists")
    for key, value in fields.items():
        setattr(camera, key, value)
    # credential handling: explicit clear wins; a provided password is
    # encrypted (never stored plaintext); omitting both keeps the old one.
    if body.clear_password:
        camera.password_encrypted = None
    elif body.password is not None:
        if not camera.username:
            raise HTTPException(
                status_code=422,
                detail="set a username before storing a stream password",
            )
        camera.password_encrypted = encrypt_text(body.password)
    _extract_inline_credentials(camera)


def _record_test_result(db: Session, camera: Camera, result: CameraTestResult) -> None:
    camera.last_test_status = result.status
    camera.last_tested_at = result.tested_at
    camera.last_error = None if result.ok else result.detail[:300]
    db.commit()


# ---------------------------------------------------------------------- routes
@router.post(
    "",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a camera (structured connection, RTSP URL and/or file)",
)
def create_camera(
    body: CameraCreate,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> CameraOut:
    existing = db.scalars(select(Camera).where(Camera.name == body.name)).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"camera name {body.name!r} already exists"
        )
    camera = Camera()
    _apply_create_fields(camera, body)
    # A legacy-style rtsp_url with inline credentials is normalized right
    # away so the secret lands encrypted and out of the URL column.
    _extract_inline_credentials(camera)
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return _camera_out(camera)


@router.get("", response_model=list[CameraOut], summary="List cameras + runtime state")
def list_cameras(
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[CameraOut]:
    cameras = db.scalars(select(Camera).order_by(Camera.id.asc())).all()
    return [_camera_out(c) for c in cameras]


@router.get("/{camera_id}", response_model=CameraOut, summary="Get one camera")
def get_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> CameraOut:
    return _camera_out(_get_or_404(db, camera_id))


@router.put("/{camera_id}", response_model=CameraOut,
            summary="Update camera config (stop it first to apply sampling_rate)")
def update_camera(
    camera_id: int,
    body: CameraUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> CameraOut:
    camera = _get_or_404(db, camera_id)
    _apply_update_fields(camera, body, db)
    db.commit()
    db.refresh(camera)
    return _camera_out(camera)


@router.delete("/{camera_id}", response_model=OkResponse,
               summary="Delete a camera (must be stopped first)")
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> OkResponse:
    camera = _get_or_404(db, camera_id)
    runtime = rc.get_camera_runtime(camera_id=camera_id)
    if runtime["state"] in ("starting", "running", "stopping", "unhealthy"):
        raise HTTPException(
            status_code=409,
            detail=f"camera is {runtime['state']} - stop it first "
            f"(POST /cameras/{camera_id}/stop)",
        )
    db.delete(camera)
    db.commit()
    # Clear any leftover runtime keys.
    try:
        r = rc.get_redis()
        with r.pipeline() as pipe:
            for key in (
                rc.cam_state_key(camera_id),
                rc.cam_stop_key(camera_id),
                rc.cam_heartbeat_key(camera_id),
                rc.cam_task_key(camera_id),
            ):
                pipe.delete(key)
            pipe.srem(settings.running_cameras_key, camera_id)
            pipe.execute()
    except redis_lib.RedisError:
        pass
    return OkResponse(detail="camera deleted")


# ------------------------------------------------------------ connection test
@router.post(
    "/test-connection",
    response_model=CameraTestResult,
    summary="Test a stream: stored camera (camera_id) or ad-hoc fields",
)
def test_connection(
    body: CameraTestRequest,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> CameraTestResult:
    """Classified probe: connected / auth_failed / unreachable / timeout /
    no_frames / unsupported / error.  Ad-hoc credentials are used for this
    request only and never persisted; every returned detail is
    credential-redacted."""
    if body.camera_id is not None:
        camera = _get_or_404(db, body.camera_id)
        if not camera.enabled:
            result = CameraTestResult(
                ok=False, status="disabled",
                detail="camera is disabled - enable it before testing",
                tested_at=utcnow(),
            )
            _record_test_result(db, camera, result)
            return result
        source = camera.build_rtsp_url()
    else:
        if body.file_path:
            source = body.file_path
        elif body.rtsp_url:
            source = body.rtsp_url
        else:
            from urllib.parse import quote

            port = body.port or 554
            path = (body.stream_path or "").strip()
            if path and not path.startswith("/"):
                path = "/" + path
            auth = ""
            if body.username:
                auth = f"{quote(body.username, safe='')}:{quote(body.password or '', safe='')}@"
            source = f"rtsp://{auth}{body.host}:{port}{path}"
    result = camera_test.test_source(source)
    if body.camera_id is not None:
        _record_test_result(db, _get_or_404(db, body.camera_id), result)
    return result


@router.post(
    "/{camera_id}/test", response_model=CameraTestResult,
    summary="Test the stored connection of one camera",
)
def test_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> CameraTestResult:
    return test_connection(CameraTestRequest(camera_id=camera_id), db=db)


# -------------------------------------------------------------- live preview
def _camera_viewer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    token: str | None = Query(
        default=None,
        description="JWT for <img>/EventSource clients that cannot send headers",
    ),
) -> str:
    """Admin-scope auth for preview endpoints.

    ``<img>``/EventSource cannot send an Authorization header, so a
    ``?token=<jwt>`` query parameter is accepted as well (same trade-off the
    live WebSocket already documents).  Teachers never pass.
    """
    if credentials is None and token:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    principal = resolve_principal(credentials, db)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return principal.username


def _camera_viewer_header(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> str:
    """Header-only admin auth for the live monitor stream.

    The browser fetches live.mjpeg with an Authorization header and parses
    the multipart body itself, so no ``?token=`` query parameter exists on
    this path at all - there is no reusable token in any URL, log line, or
    browser history entry associated with the video stream.
    """
    principal = resolve_principal(credentials, db)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return principal.username


@router.get(
    "/{camera_id}/snapshot",
    summary="One live JPEG frame (admin only, credentials never exposed)",
)
def camera_snapshot(
    camera_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(_camera_viewer),
) -> Response:
    """One JPEG frame for the camera preview card.

    When the pipeline is RUNNING this reads the shared publication from
    Redis — the browser never triggers a second RTSP capture (which would
    compete with the pipeline's FrameReader for the camera's limited
    concurrent-session slots and knock the pipeline offline).  A direct
    source probe is used only when the pipeline is stopped, so an admin
    can still verify connectivity before starting it.
    """
    camera = _get_or_404(db, camera_id)
    runtime = rc.get_camera_runtime(camera_id=camera_id)
    if runtime["state"] == "running" and runtime["running"]:
        # Pipeline owns the camera's RTSP slot.  Never open a second capture
        # here — it would compete with the pipeline's FrameReader.  Either
        # serve the shared frame or explain why there is not one yet.
        try:
            jpeg = rc.read_live_frame(rc.get_binary_redis(), camera_id)
        except redis_lib.RedisError as exc:
            raise HTTPException(
                status_code=503, detail=f"frame bus unavailable: {exc}"
            )
        if jpeg:
            return Response(content=jpeg, media_type="image/jpeg")
        meta = rc.read_live_meta(rc.get_redis(), camera_id)
        raise HTTPException(
            status_code=503,
            detail=(meta.get("error") or "").strip()
            or "pipeline is running but no frames have arrived yet - "
            "the source may still be connecting",
        )
    # Pipeline not running: direct probe (setup / connectivity check only).
    jpeg = camera_test.read_frame_jpeg(camera.build_rtsp_url())
    if jpeg is None:
        raise HTTPException(
            status_code=502,
            detail=camera.last_error or "could not decode a frame from this source",
        )
    return Response(content=jpeg, media_type="image/jpeg")


@router.get(
    "/{camera_id}/stream.mjpeg",
    summary="MJPEG preview stream (deprecated — use live.mjpeg; token query allowed for <img>)",
)
def camera_stream(
    camera_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(_camera_viewer),
) -> StreamingResponse:
    """Legacy MJPEG preview (kept for backward compatibility).

    When the pipeline is RUNNING this serves the shared publication from
    Redis (same as live.mjpeg) and opens NO camera connection.  A direct
    source probe is used only when the pipeline is stopped.  Prefer
    ``live.mjpeg`` (header auth, no query tokens) for new integrations.
    """
    camera = _get_or_404(db, camera_id)
    runtime = rc.get_camera_runtime(camera_id=camera_id)
    if runtime["state"] == "running" and runtime["running"]:
        # Pipeline owns the camera's RTSP slot — serve the shared
        # publication and open NO camera connection of our own.
        try:
            r_bin = rc.get_binary_redis()
            r = rc.get_redis()
        except redis_lib.RedisError as exc:
            raise HTTPException(status_code=503, detail=f"frame bus unavailable: {exc}")
        if rc.read_live_frame(r_bin, camera_id) is None:
            meta = rc.read_live_meta(r, camera_id)
            raise HTTPException(
                status_code=503,
                detail=(meta.get("error") or "").strip()
                or "pipeline is running but no frames have arrived yet - "
                "the source may still be connecting",
            )
        interval = max(0.2, 1.0 / max(1, settings.live_mjpeg_fps))
        max_parts = max(1, settings.live_mjpeg_fps) * 600
        return StreamingResponse(
            iter_live_parts(r_bin, r, camera_id, interval, max_parts),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    # Pipeline not running: direct probe (setup / connectivity check only).
    source = camera.build_rtsp_url()
    interval = max(0.2, 1.0 / max(1, settings.camera_stream_fps))

    def _frames():
        import cv2

        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        failures = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures > 5:
                        break
                    time_mod.sleep(interval)
                    continue
                failures = 0
                ok_enc, buf = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), settings.jpeg_quality]
                )
                if not ok_enc:
                    continue
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(buf)).encode() + b"\r\n\r\n"
                    + buf.tobytes() + b"\r\n"
                )
                time_mod.sleep(interval)
        finally:
            cap.release()

    return StreamingResponse(
        _frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _live_part(camera_id: int, jpeg: bytes, frame_ts: float, annotated: bool) -> bytes:
    """One multipart body: the JPEG plus the metadata the UI needs.

    ``X-Frame-Kind`` lets the browser tell a recognition-annotated frame from
    a plain (but fresher) capture-tap frame, so it can decide whether a
    slightly older annotated frame is worth showing over a newer raw one.
    """
    return (
        b"--frame\r\nContent-Type: image/jpeg\r\n"
        b"X-Camera-Id: " + str(camera_id).encode() + b"\r\n"
        b"X-Frame-Ts: " + repr(float(frame_ts)).encode() + b"\r\n"
        b"X-Frame-Kind: " + (b"annotated" if annotated else b"raw") + b"\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
        + jpeg + b"\r\n"
    )


def iter_live_parts(r_bin, r, camera_id: int, interval: float, max_parts: int):
    """Yield MJPEG parts from the shared publication (bounded by max_parts).

    Delivery is CHANGE-DRIVEN: the frame bus is polled every
    ``live_mjpeg_poll_ms`` and a part is emitted only when the published
    frame is actually new (its own timestamp moved).  That keeps
    capture-to-display latency low without padding the stream with duplicate
    JPEGs; while the source is idle the keep-alive re-sends a part so clients
    can still distinguish "quiet" from "dead".  ``interval`` stays the maximum
    emit spacing and ``max_parts`` the total part budget, so callers (and the
    existing tests) keep their contract.
    """
    poll_s = max(0.005, settings.live_mjpeg_poll_ms / 1000.0)
    keepalive_s = max(float(interval), float(settings.live_mjpeg_keepalive_s))
    min_emit_gap = max(0.0, float(interval))
    viewers_key = f"cam:{camera_id}:viewers"
    claimed = False
    try:
        r.incr(viewers_key)
        r.expire(viewers_key, 60)
        claimed = True
    except redis_lib.RedisError:
        pass
    last_ts: float | None = None
    last_emit = 0.0
    emitted = 0
    try:
        while emitted < max(1, max_parts):
            frame = rc.read_live_frame_ex(r_bin, camera_id)
            if frame is None:
                # Source died or pipeline stopped mid-stream: end here.  The
                # UI falls back to live-status for the real reason instead of
                # showing a frozen frame as live.
                break
            jpeg, ts, annotated = frame
            now = time_mod.monotonic()
            changed = ts is None or ts != last_ts
            within_gap = (now - last_emit) < min_emit_gap
            # Plain capture frames are rate-limited to the advertised FPS, but
            # a recognition-annotated frame ALWAYS goes out: it carries new
            # information (the overlay), and dropping it would silently make
            # the recognition overlay rare or invisible.
            if (not changed and (now - last_emit) < keepalive_s) or (
                within_gap and not (changed and annotated)
            ):
                time_mod.sleep(poll_s)
                continue
            if ts is None:
                # Legacy value without its own timestamp: fall back to the
                # status hash so the browser's stale-frame guard still works.
                try:
                    meta = r.hgetall(rc.cam_live_key(camera_id)) or {}
                    ts = float(meta.get("ts", "0") or 0)
                except (redis_lib.RedisError, TypeError, ValueError):
                    ts = 0.0
            yield _live_part(camera_id, jpeg, ts, annotated)
            last_ts = ts
            last_emit = now
            emitted += 1
            try:
                r.expire(viewers_key, 60)
            except redis_lib.RedisError:
                pass
    finally:
        if claimed:
            try:
                # Clamp at zero: a viewer whose generator is finalised long
                # after its key expired must never drive the counter negative
                # (decr on a missing key would CREATE it at -1).
                if r.decr(viewers_key) < 0:
                    r.set(viewers_key, 0, ex=60)
            except redis_lib.RedisError:
                pass


@router.get(
    "/{camera_id}/live.mjpeg",
    summary="Live annotated monitor stream (shared pipeline frames, header auth)",
    responses={
        200: {"content": {"multipart/x-mixed-replace; boundary=frame": {}}},
        409: {"description": "Camera pipeline is not running"},
        503: {"description": "Running but no frames yet, or frame bus down"},
    },
)
def camera_live(
    camera_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(_camera_viewer_header),
) -> StreamingResponse:
    """Browser-compatible live video with recognition overlays.

    Frames come from the RUNNING pipeline's shared Redis publication - this
    endpoint opens NO camera connection itself, so N viewers never create N
    captures.  Parts carry ``X-Frame-Ts`` / ``X-Camera-Id`` so the UI can
    detect stale frames and camera switches.  Auth is the Authorization
    header (the UI fetches and parses the multipart body); no query-string
    tokens, no camera credentials anywhere near the browser.
    """
    camera = _get_or_404(db, camera_id)
    runtime = rc.get_camera_runtime(camera_id=camera_id)
    if runtime["state"] != "running" or not runtime["running"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"camera pipeline is {runtime['state']} - start the camera "
                "first; nothing is streamed while it is stopped"
            ),
        )
    try:
        r_bin = rc.get_binary_redis()
        r = rc.get_redis()
    except redis_lib.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"frame bus unavailable: {exc}")
    interval = max(1.0 / 60.0, 1.0 / max(1, settings.live_mjpeg_fps))

    # Peek first: report WHY there is no video as JSON instead of an empty
    # stream the UI cannot diagnose.
    if rc.read_live_frame(r_bin, camera_id) is None:
        meta = rc.read_live_meta(r, camera_id)
        err = (meta.get("error") or "").strip()
        raise HTTPException(
            status_code=503,
            detail=(
                err
                or "pipeline is running but no frames have arrived yet - "
                "the source may still be connecting"
            ),
        )
    # NOTE: the endpoint is sync so it runs in the threadpool and never
    # blocks the event loop.  Disconnects surface as send-side failures
    # that unwind the generator - its finally always releases the viewer
    # slot.  The part bound (~10 min at 6 fps) terminates generators nobody
    # is reading anymore; the UI reconnects long before that.
    max_parts = max(1, settings.live_mjpeg_fps) * 600
    return StreamingResponse(
        iter_live_parts(r_bin, r, camera_id, interval, max_parts),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Camera-Id": str(camera_id),
        },
    )


@router.get(
    "/{camera_id}/live-status",
    summary="Monitor status: freshness, inference state, viewers (header auth)",
)
def camera_live_status(
    camera_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(_camera_viewer_header),
) -> dict:
    """JSON companion to live.mjpeg for the monitor status line."""
    camera = _get_or_404(db, camera_id)
    runtime = rc.get_camera_runtime(camera_id=camera_id)
    try:
        r = rc.get_redis()
        meta = rc.read_live_meta(r, camera_id)
        viewers = r.get(f"cam:{camera_id}:viewers")
    except redis_lib.RedisError:
        meta, viewers = {}, None
    has_frame = False
    try:
        has_frame = rc.read_live_frame(rc.get_binary_redis(), camera_id) is not None
    except redis_lib.RedisError:
        pass
    frame_age_s: float | None = None
    try:
        ts = float((meta.get("ts") or "0"))
        if ts > 0:
            frame_age_s = round(time_mod.time() - ts, 1)
    except (TypeError, ValueError):
        pass
    # The video slot can be fresher than the recognition meta (the capture
    # tap publishes independently of inference), so report both ages: this is
    # what makes "how old is the picture I am looking at" observable.
    video_age_s: float | None = None
    video_kind: str | None = None
    try:
        frame = rc.read_live_frame_ex(rc.get_binary_redis(), camera_id)
    except redis_lib.RedisError:
        frame = None
    if frame is not None:
        _jpeg, vts, annotated = frame
        video_kind = "annotated" if annotated else "raw"
        if vts is None:  # legacy value: fall back to the status hash
            try:
                vts = float((meta.get("ts") or "0"))
            except (TypeError, ValueError):
                vts = 0.0
        if vts > 0:
            video_age_s = round(time_mod.time() - vts, 1)
    startup_deadline_at = runtime.get("startup_deadline_at")
    if startup_deadline_at is not None and startup_deadline_at > 0:
        startup_deadline_at = round(startup_deadline_at, 1)
    error = runtime.get("error")
    return {
        "camera_id": camera_id,
        "name": camera.name,
        "state": runtime["state"],
        "running": runtime["running"],
        "has_frame": has_frame,
        "frame_age_s": frame_age_s,
        "video_age_s": video_age_s,
        "video_kind": video_kind,
        "fps": meta.get("fps"),
        "infer_ok": meta.get("infer_ok") == "1",
        "infer_ms": meta.get("infer_ms") or None,
        "faces": meta.get("faces"),
        "error": error or meta.get("error") or None,
        "viewers": int(viewers) if viewers is not None else 0,
        "startup_deadline_at": startup_deadline_at,
    }


# ------------------------------------------------------------- start / stop
@router.post("/{camera_id}/start", response_model=StartStopResponse,
             summary="Start the processing pipeline for a camera")
def start_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> StartStopResponse:
    camera = _get_or_404(db, camera_id)
    if not camera.enabled:
        raise HTTPException(
            status_code=409,
            detail="camera is disabled - enable it (PUT /cameras/{id} "
            "with enabled=true) before starting",
        )
    r = _redis_or_503()

    runtime = rc.get_camera_runtime(r=r, camera_id=camera_id)
    # Leftover state from a task that no longer exists (worker/API/container
    # restart, broker loss, crash): clear it and carry on instead of refusing
    # forever.  clear_stale_camera_state() refuses to touch a camera whose
    # heartbeat is still fresh, so a live pipeline can never be clobbered.
    if runtime["stale"] or runtime["state"] == "failed":
        cleared = rc.clear_stale_camera_state(r, camera_id, runtime)
        if cleared:
            logger.info(
                "cleared stale camera state before start",
                extra={
                    "camera_id": camera_id,
                    "stored_state": runtime["stored_state"],
                    "slot": runtime["slot"],
                },
            )
        runtime = rc.get_camera_runtime(r=r, camera_id=camera_id)

    if runtime["state"] in ("starting", "running"):
        # Genuinely live (fresh heartbeat): start is idempotent, so report the
        # existing task instead of dispatching a second pipeline.  No new slot,
        # no new Celery task, no second RTSP connection.
        return StartStopResponse(
            camera_id=camera_id,
            state=runtime["state"],
            slot=runtime["slot"],
            task_id=runtime["task_id"],
            detail=f"camera already {runtime['state']} (slot={runtime['slot']}) - "
                   "start is idempotent; stop it first to restart",
        )
    if runtime["state"] in ("stopping", "unhealthy"):
        raise HTTPException(
            status_code=409,
            detail=f"camera is {runtime['state']} (slot={runtime['slot']}); "
                   "stop it first if you want to restart it",
        )

    slot = _acquire_slot(r, camera_id)
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"all {settings.camera_slots} camera slots are busy - "
                   "stop another camera or raise CAMERA_SLOTS",
        )

    r.delete(rc.cam_stop_key(camera_id))
    r.delete(rc.cam_error_key(camera_id))
    r.set(rc.cam_state_key(camera_id), "starting", ex=settings.camera_state_ttl_s)
    # Claim liveness NOW, not when the worker eventually picks the task up.
    # _acquire_slot() treats "slot owner without a heartbeat" as a dead worker
    # and reclaims it, so without this a second /start issued inside the
    # dispatch window (worker busy / starting up) would DELETE the first
    # camera's slot and let both pipelines share one slot queue - silently
    # breaking the CAMERA_SLOTS admission limit.  heartbeat_ttl_s then acts as
    # the pickup grace: if no worker consumes the task within that window the
    # slot becomes reclaimable again, which is exactly what we want.
    rc.touch_heartbeat(r, camera_id)

    queue_name = settings.slot_queue(slot)
    try:
        result = celery_app.send_task(
            "camera.run", args=[camera_id, slot], queue=queue_name
        )
    except Exception as exc:  # noqa: BLE001 - broker down
        with r.pipeline() as pipe:
            pipe.delete(rc.cam_state_key(camera_id))
            pipe.delete(rc.cam_heartbeat_key(camera_id))
            pipe.delete(rc.cam_slot_key(slot))
            pipe.execute()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"could not enqueue camera task (broker down?): {exc}",
        ) from exc

    r.set(rc.cam_task_key(camera_id), result.id, ex=86400)
    r.sadd(settings.running_cameras_key, camera_id)
    # Bounded startup: the worker must reach RUNNING (first decoded frame)
    # within camera_startup_timeout_s.  Missing the deadline is reported as
    # FAILED with the real capture error instead of lingering in STARTING.
    try:
        r.set(
            rc.cam_startup_deadline_key(camera_id),
            str(time_mod.time() + settings.camera_startup_timeout_s),
            ex=int(settings.camera_startup_timeout_s) + 120,
        )
    except redis_lib.RedisError:
        pass
    publish(
        r,
        camera_state_event(
            camera_id=camera.id, name=camera.name, state="starting", slot=slot
        ),
    )
    logger.info("camera start requested", extra={"camera_id": camera_id, "slot": slot})
    return StartStopResponse(
        camera_id=camera_id,
        state="starting",
        slot=slot,
        task_id=result.id,
        startup_deadline_s=settings.camera_startup_timeout_s,
    )


@router.post("/{camera_id}/stop", response_model=StartStopResponse,
             summary="Stop the processing pipeline (force=true revokes the task)")
def stop_camera(
    camera_id: int,
    force: bool = Query(default=False,
                        description="Immediately revoke the Celery task and clear state"),
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> StartStopResponse:
    camera = _get_or_404(db, camera_id)
    r = _redis_or_503()

    runtime = rc.get_camera_runtime(r=r, camera_id=camera_id)
    if runtime["state"] == "stopped" and not runtime["running"]:
        return StartStopResponse(
            camera_id=camera_id, state="stopped", detail="already stopped"
        )

    r.set(rc.cam_stop_key(camera_id), "1", ex=3600)
    r.set(rc.cam_state_key(camera_id), "stopping", ex=86400)

    if force:
        task_id = runtime["task_id"]
        if task_id:
            try:
                celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
            except Exception as exc:  # noqa: BLE001
                logger.warning("revoke failed: %s", exc)
        with r.pipeline() as pipe:
            for key in (
                rc.cam_state_key(camera_id),
                rc.cam_stop_key(camera_id),
                rc.cam_heartbeat_key(camera_id),
                rc.cam_task_key(camera_id),
            ):
                pipe.delete(key)
            pipe.srem(settings.running_cameras_key, camera_id)
            pipe.execute()
        slot = runtime["slot"]
        if slot is not None:
            owner = r.get(rc.cam_slot_key(slot))
            if owner is not None and int(owner) == camera_id:
                r.delete(rc.cam_slot_key(slot))
        state = "stopped"
        publish(
            r,
            camera_state_event(
                camera_id=camera.id, name=camera.name, state="stopped", slot=slot
            ),
        )
    else:
        state = "stopping"
        publish(
            r,
            camera_state_event(
                camera_id=camera.id,
                name=camera.name,
                state="stopping",
                slot=runtime["slot"],
            ),
        )

    logger.info(
        "camera stop requested",
        extra={"camera_id": camera_id, "force": force},
    )
    return StartStopResponse(
        camera_id=camera_id,
        state=state,
        slot=runtime["slot"],
        task_id=runtime["task_id"],
        detail=None if force else "stop flag set; poll GET /cameras/"
        f"{camera_id} until state=stopped",
    )
