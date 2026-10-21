"""Camera connection testing with classified, credential-redacted outcomes.

Never logs, stores or returns a credentialed URL: every diagnostic string
passes through :func:`redact` before it reaches a response, the database or
the log stream.  Classification strategy:

1. **ffmpeg** (when on PATH) - its RTSP diagnostics distinguish
   authentication failures (``401 Unauthorized``) from network and protocol
   problems far better than OpenCV.
2. **OpenCV fallback** - TCP pre-probe (refused vs timeout) plus
   ``cv2.VideoCapture`` open/decode.  When the transport works but the
   stream cannot be opened, the result is reported as ``error`` with an
   honest "check credentials / stream path" hint - OpenCV cannot prove an
   authentication failure, and we never claim one it did not report.
"""

from __future__ import annotations

import logging
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..config import settings
from ..schemas import CameraTestResult
from ..timeutil import utcnow

logger = logging.getLogger("app.camera_test")

# rtsp://user:password@host -> rtsp://***@host  (also rtsps://)
_USERINFO_RE = re.compile(r"(?i)\b(rtsps?://)[^/@\s]+@")


def redact(text: str | None) -> str | None:
    """Strip ``user:pass@`` userinfo from any diagnostic text."""
    if not text:
        return text
    return _USERINFO_RE.sub(r"\1***@", text)


def _split_source(source: str) -> tuple[str, str, int]:
    """Return (scheme, host, port) for an rtsp(s) URL."""
    parts = urlsplit(source)
    host = parts.hostname or ""
    default = 554 if parts.scheme.lower() == "rtsp" else 322
    return parts.scheme.lower(), host, parts.port or default


def _tcp_probe(host: str, port: int, timeout: float) -> str:
    """'ok' | 'refused' | 'timeout' - coarse transport classification."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok"
    except (ConnectionRefusedError, OSError) as exc:
        err = str(exc).lower()
        if isinstance(exc, ConnectionRefusedError) or "refused" in err:
            return "refused"
        if "timed out" in err or "timeout" in err:
            return "timeout"
        if "name or service" in err or "getaddrinfo" in err or "nodename" in err:
            return "dns"
        return "refused"


def _test_with_ffmpeg(source: str, timeout: float) -> CameraTestResult | None:
    """Probe one frame through ffmpeg; ``None`` when ffmpeg is unavailable."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        return None
    cmd = [
        exe, "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", settings.rtsp_transport,
        "-stimeout", str(int(timeout * 1_000_000)),
        "-i", source, "-frames:v", "1", "-f", "null", "-",
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, capture_output=True, timeout=timeout + 5
        )
    except subprocess.TimeoutExpired:
        return CameraTestResult(
            ok=False, status="timeout",
            detail=f"stream did not deliver a frame within {timeout:.0f}s",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            tested_at=utcnow(),
        )
    latency = round((time.perf_counter() - started) * 1000, 1)
    stderr = redact((proc.stderr or b"").decode("utf-8", "replace")).strip()
    low = stderr.lower()
    if proc.returncode == 0:
        return CameraTestResult(
            ok=True, status="connected",
            detail="stream reachable; one frame decoded",
            latency_ms=latency, tested_at=utcnow(),
        )
    if "401" in low or "unauthorized" in low or "authorization failed" in low:
        status, detail = "auth_failed", (
            "camera rejected the credentials (HTTP 401) - check username/password"
        )
    elif "connection refused" in low or "no route to host" in low or "network is unreachable" in low:
        status, detail = "unreachable", "host/port refused the connection - check address and port"
    elif "timed out" in low or "timeout" in low:
        status, detail = "timeout", "no response within the timeout - camera offline or filtered"
    elif "404" in low or "not found" in low or "405" in low or "method not allowed" in low:
        status, detail = "unsupported", (
            "server answered but the stream path/method is not supported - "
            "check the RTSP path"
        )
    elif "dns" in low or "name or service" in low or "nodename nor servname" in low:
        status, detail = "unreachable", "hostname could not be resolved"
    else:
        status, detail = "error", f"stream could not be opened: {stderr[:160] or 'no diagnostic'}"
    return CameraTestResult(
        ok=False, status=status, detail=detail, latency_ms=latency, tested_at=utcnow()
    )


def _test_with_opencv(source: str, timeout: float) -> CameraTestResult:
    """Fallback probe: TCP pre-probe + cv2 open/decode."""
    import cv2

    started = time.perf_counter()
    scheme, host, port = _split_source(source)
    if scheme not in ("rtsp", "rtsps"):
        return CameraTestResult(
            ok=False, status="unsupported",
            detail=f"unsupported source scheme {scheme!r} (expected rtsp:// or rtsps://)",
            tested_at=utcnow(),
        )
    probe = _tcp_probe(host, port, timeout=min(timeout, 5.0))
    if probe == "dns":
        return CameraTestResult(
            ok=False, status="unreachable",
            detail="hostname could not be resolved", tested_at=utcnow(),
        )
    if probe == "refused":
        return CameraTestResult(
            ok=False, status="unreachable",
            detail="host/port refused the connection - check address and port",
            tested_at=utcnow(),
        )
    if probe == "timeout":
        return CameraTestResult(
            ok=False, status="timeout",
            detail=f"no TCP response from {host}:{port} within 5s - camera offline or filtered",
            tested_at=utcnow(),
        )

    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    try:
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout * 1000))
        if not cap.isOpened():
            latency = round((time.perf_counter() - started) * 1000, 1)
            return CameraTestResult(
                ok=False, status="error",
                detail=(
                    "TCP connected but the stream could not be opened - verify the "
                    "username, password and RTSP stream path"
                ),
                latency_ms=latency, tested_at=utcnow(),
            )
        ok, frame = cap.read()
        latency = round((time.perf_counter() - started) * 1000, 1)
        if not ok or frame is None:
            return CameraTestResult(
                ok=False, status="no_frames",
                detail="stream opened but delivered no decodable frame",
                latency_ms=latency, tested_at=utcnow(),
            )
        h, w = frame.shape[:2]
        return CameraTestResult(
            ok=True, status="connected",
            detail="stream reachable; one frame decoded",
            latency_ms=latency, frame_size=f"{w}x{h}", tested_at=utcnow(),
        )
    finally:
        cap.release()


def _test_file_source(file_path: str) -> CameraTestResult:
    """Local video file: exists + decodable."""
    import cv2

    started = time.perf_counter()
    p = Path(file_path).expanduser()
    if not p.is_file():
        return CameraTestResult(
            ok=False, status="unreachable",
            detail=f"video file not found: {p.name}", tested_at=utcnow(),
        )
    cap = cv2.VideoCapture(str(p))
    try:
        ok, frame = cap.read()
        latency = round((time.perf_counter() - started) * 1000, 1)
        if not ok or frame is None:
            return CameraTestResult(
                ok=False, status="no_frames",
                detail="file exists but no frame could be decoded",
                latency_ms=latency, tested_at=utcnow(),
            )
        h, w = frame.shape[:2]
        return CameraTestResult(
            ok=True, status="connected",
            detail=f"file readable; first frame decoded ({w}x{h})",
            latency_ms=latency, frame_size=f"{w}x{h}", tested_at=utcnow(),
        )
    finally:
        cap.release()


def test_source(source: str | None) -> CameraTestResult:
    """Classified connection test for a resolved source string."""
    if not source:
        return CameraTestResult(
            ok=False, status="error", detail="no stream source configured",
            tested_at=utcnow(),
        )
    timeout = float(settings.camera_connect_timeout_s)
    if not source.lower().startswith(("rtsp://", "rtsps://")):
        return _test_file_source(source)
    result = _test_with_ffmpeg(source, timeout)
    if result is not None:
        return result
    try:
        return _test_with_opencv(source, timeout)
    except Exception as exc:  # noqa: BLE001 - cv2 import/driver problems
        logger.warning("opencv probe failed: %s", redact(str(exc)))
        return CameraTestResult(
            ok=False, status="error",
            detail=f"probe failed: {redact(str(exc))[:160]}",
            tested_at=utcnow(),
        )


def read_frame_jpeg(source: str | None, timeout: float | None = None) -> bytes | None:
    """Decode one JPEG frame from a source (preview snapshots)."""
    import cv2

    if not source:
        return None
    timeout = timeout or float(settings.camera_connect_timeout_s)
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    try:
        if not cap.isOpened():
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                ok_enc, buf = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), settings.jpeg_quality]
                )
                if ok_enc:
                    return buf.tobytes()
            time.sleep(0.1)
        return None
    finally:
        cap.release()
