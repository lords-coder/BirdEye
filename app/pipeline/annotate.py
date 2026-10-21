"""CCTV-style overlays drawn on the pipeline's own frames.

The worker already decodes each frame and already runs detection+embedding
through the shared inference service; this module only *draws* those
results (bounding box + identity + presence state) onto a downscaled copy
for the browser monitor.  No second recognition pipeline exists: matching
reuses :class:`app.matching.EmbeddingIndex`, attendance state comes from
the presence/session/record tables (reads only - never recomputed here).

Visual contract
--------------
* green  = engine-confirmed enrolled identity (name + RECOGNIZED)
* amber  = face the engine could NOT confirm (UNKNOWN / UNVERIFIED)
* sub-labels show live presence (IN CLASS · m MIN, VERIFYING…), and the
  official period status (PRESENT / ...) only when a stored record says so.
  A match alone never prints PRESENT.
* inference outage  = plain frame, no boxes (caller flags infer_ok=false);
  "no boxes" therefore never masquerades as "no faces".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..matching import EmbeddingIndex
from ..models import AttendanceRecord, AttendanceSession, Period, PresenceState, Student
from ..timeutil import local_date, to_local

logger = logging.getLogger("app.pipeline.annotate")

GREEN = (46, 204, 64)  # BGR
AMBER = (0, 165, 255)  # BGR
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def _today_sessions_seconds(db: Session, student_id: int) -> int:
    day = local_date()
    rows = db.scalars(
        select(AttendanceSession).where(
            AttendanceSession.student_id == student_id,
            AttendanceSession.date == day,
        )
    ).all()
    now = datetime.now(timezone.utc)
    total = 0
    for s in rows:
        entry = s.entry_time
        if entry.tzinfo is None:
            entry = entry.replace(tzinfo=timezone.utc)
        end = s.exit_time
        if end is None:
            end = now
        elif end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end > entry:
            total += int((end - entry).total_seconds())
    return total


def _active_period(db: Session, student: Student | None):
    """Period covering right now for the student's section (or None)."""
    now_local = to_local(datetime.now(timezone.utc))
    weekday_bit = 1 << now_local.weekday()
    local_t = now_local.time()
    section = student.section if student is not None else None
    for p in db.scalars(select(Period).order_by(Period.start_time.asc())).all():
        if p.section is not None and p.section != section:
            continue
        if not (p.days_bitmask & weekday_bit):
            continue
        if p.start_time <= local_t < p.end_time:
            return p
    return None


def _threshold_for(period: Period | None) -> tuple[int, str | None]:
    """(threshold seconds, period name) without writing anything."""
    if period is None:
        return int(settings.attendance_default_required_minutes) * 60, None
    from ..services import attendance_engine as _eng

    start, end = _eng.period_window(period, local_date())
    window_s = int((end - start).total_seconds())
    return _eng.threshold_seconds(period, window_s), period.name


def build_detections(
    db: Session,
    index: EmbeddingIndex,
    faces: list[dict],
) -> list[dict]:
    """Match display-frame faces -> drawable records (CPU cosine only).

    The expensive detection+embedding already happened in the inference
    service; this is the same matcher the attendance path uses.
    """
    out: list[dict] = []
    for face in faces:
        bbox = face.get("bbox")
        if not bbox:
            continue
        embedding = face.get("embedding")
        if not embedding:
            out.append({"bbox": bbox, "kind": "unverified"})
            continue
        match = index.match(embedding)
        if match.student_id is None:
            out.append(
                {"bbox": bbox, "kind": "unknown", "score": float(match.score)}
            )
            continue
        student = db.get(Student, int(match.student_id))
        name = student.name if student else f"#{match.student_id}"
        state_row = db.scalar(
            select(PresenceState).where(
                PresenceState.student_id == int(match.student_id)
            )
        )
        pstate = state_row.state if state_row else "OUTSIDE"
        minutes = _today_sessions_seconds(db, int(match.student_id))
        period = _active_period(db, student)
        threshold_s, _ = _threshold_for(period)
        official = None
        if period is not None:
            rec = db.scalar(
                select(AttendanceRecord).where(
                    AttendanceRecord.period_id == period.id,
                    AttendanceRecord.student_id == int(match.student_id),
                    AttendanceRecord.date == local_date(),
                )
            )
            if rec is not None and rec.effective_status:
                official = rec.effective_status
        out.append(
            {
                "bbox": bbox,
                "kind": "recognized",
                "student_id": int(match.student_id),
                "name": name,
                "score": float(match.score),
                "presence": pstate,
                "minutes_today": minutes // 60,
                "threshold_s": threshold_s,
                "official": official,
            }
        )
    return out


def _label_lines(det: dict) -> tuple[str, str | None]:
    if det["kind"] == "unverified":
        return "UNVERIFIED", None
    if det["kind"] == "unknown":
        return "UNKNOWN / UNVERIFIED", None
    main = f"{det['name']} | RECOGNIZED"
    sub: str | None = None
    if det.get("official") == "PRESENT":
        sub = "PRESENT"
    elif det.get("presence") == "INSIDE":
        need_min = max(0, -(-int(det.get("threshold_s", 0)) // 60))
        sub = f"IN CLASS · {det.get('minutes_today', 0)}/{need_min} MIN"
    elif det.get("presence") in ("ENTRY_PENDING", "EXIT_PENDING", "NEEDS_REVIEW"):
        sub = "VERIFYING…"
    return main, sub


def annotate(image, detections: list[dict], src_size: tuple[int, int] | None = None):  # noqa: ANN001, ANN202
    """Draw boxes+labels on a BGR frame; return the annotated copy.

    ``src_size`` is the (width, height) of the frame the inference bboxes
    refer to; boxes are scaled onto ``image`` so they stay glued to faces
    even when the published copy is downscaled.
    """
    import cv2
    import numpy as np

    img = np.asarray(image)
    h, w = img.shape[:2]
    out = img.copy()
    src_w, src_h = src_size or (w, h)
    src_w, src_h = max(1, int(src_w)), max(1, int(src_h))
    sx, sy = w / src_w, h / src_h
    for det in detections:
        try:
            x1, y1, x2, y2 = (float(v) for v in det["bbox"][:4])
        except (TypeError, ValueError):
            continue
        x1, y1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
        x2, y2 = min(w - 1, int(x2 * sx)), max(0, int(y2 * sy))
        if x2 <= x1 or y2 <= y1:
            continue
        color = GREEN if det["kind"] == "recognized" else AMBER
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        main, sub = _label_lines(det)
        lines = [main] + ([sub] if sub else [])
        font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
        ly = max(0, y1 - 8 - 20 * len(lines))
        for i, text in enumerate(lines):
            (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
            yb = ly + i * 20
            cv2.rectangle(out, (x1, yb), (x1 + tw + 6, yb + th + 8), BLACK, -1)
            cv2.putText(out, text, (x1 + 3, yb + th + 4), font, scale, WHITE, thick,
                        cv2.LINE_AA)
    return out


def downscale(image, width: int):  # noqa: ANN001, ANN202
    import cv2

    h, w = image.shape[:2]
    if w <= width:
        return image
    scale = width / float(w)
    return cv2.resize(image, (width, int(h * scale)), interpolation=cv2.INTER_AREA)


def encode_jpeg(image, quality: int) -> bytes | None:  # noqa: ANN202
    import cv2

    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    return buf.tobytes() if ok else None
