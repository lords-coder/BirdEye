"""Per-period attendance calculation from confirmed presence intervals.

Policy (documented, configurable):

* A period's scheduled window is ``[start_time, end_time)`` on the given
  local calendar date, converted to UTC for comparison.
* Every confirmed interval (attendance session) of the student that day is
  clipped to the window; time before the lecture starts or after it ends
  never counts.  Intervals are merged before summing, so overlapping or
  duplicated observations cannot double-count a minute.
* The required threshold is ``max(minutes, ratio x scheduled length)`` where
  the minutes/ratio come from the period when set, else from the global
  defaults (``ATTENDANCE_DEFAULT_REQUIRED_MINUTES`` /
  ``ATTENDANCE_DEFAULT_REQUIRED_RATIO``).  Default policy: **30 minutes of a
  60-minute lecture**.
* Machine status: ``SCHEDULED`` (before start), ``IN_PROGRESS`` (running,
  provisional - a running lecture never marks everyone absent),
  ``PRESENT``/``ABSENT`` (after the window closes), or ``NEEDS_REVIEW`` when
  ambiguous evidence (an open presence-review event) could flip the result.
* An open (unresolved) session counts its presence up to *now* - clipped at
  the period end - and its missing exit is never invented; the session keeps
  ``review_status='unresolved'`` until a human or a later observation
  resolves it.
* ``effective_status`` = the latest override when one exists, else the
  machine result once it is final.  Recomputation preserves overrides.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AttendanceOverride,
    AttendanceRecord,
    AttendanceSession,
    Period,
    PresenceEvent,
    Student,
)
from ..timeutil import local_tz, utcnow

logger = logging.getLogger("app.attendance_engine")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def period_window(period: Period, day: date) -> tuple[datetime, datetime]:
    """Scheduled ``[start, end)`` for the local date as aware UTC."""
    tz = local_tz()
    start = datetime.combine(day, period.start_time, tzinfo=tz)
    end = datetime.combine(day, period.end_time, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def threshold_seconds(period: Period, window_seconds: int) -> int:
    """Applied policy threshold: max(configured minutes, configured ratio)."""
    minutes = (
        period.required_minutes
        if period.required_minutes is not None
        else settings.attendance_default_required_minutes
    )
    ratio = (
        period.required_ratio
        if period.required_ratio is not None
        else settings.attendance_default_required_ratio
    )
    candidates: list[int] = []
    if minutes:
        candidates.append(int(minutes) * 60)
    if ratio:
        candidates.append(int(window_seconds * ratio))
    return max(candidates) if candidates else 0


def clipped_intervals(
    sessions: list[AttendanceSession],
    start: datetime,
    end: datetime,
    now: datetime,
) -> list[tuple[datetime, datetime]]:
    """Merge each session's presence with the window (inclusive start,
    exclusive end)."""
    raw: list[tuple[datetime, datetime]] = []
    for s in sessions:
        entry = _as_utc(s.entry_time)
        if s.exit_time is not None:
            exit_t = _as_utc(s.exit_time)
        else:
            # Unresolved/open session: counts until now, never past the
            # scheduled end and never past the current time.
            exit_t = min(now, end)
        if exit_t <= entry:
            continue
        o_start = max(entry, start)
        o_end = min(exit_t, end)
        if o_end > o_start:
            raw.append((o_start, o_end))
    raw.sort()
    merged: list[tuple[datetime, datetime]] = []
    for o_start, o_end in raw:
        if merged and o_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], o_end))
        else:
            merged.append((o_start, o_end))
    return merged


def _needs_review(
    db: Session, student_id: int, start: datetime, end: datetime
) -> bool:
    """Ambiguous evidence inside the window that could flip the result?"""
    row = db.scalars(
        select(PresenceEvent)
        .where(
            PresenceEvent.student_id == student_id,
            PresenceEvent.review_status == "open",
            PresenceEvent.event_time >= start,
            PresenceEvent.event_time <= min(end, utcnow()),
        )
        .limit(1)
    ).first()
    return row is not None


def compute_machine_status(
    *,
    present_seconds: int,
    threshold: int,
    start: datetime,
    end: datetime,
    now: datetime,
    ambiguous: bool,
) -> str:
    if now < start:
        return "SCHEDULED"
    if present_seconds >= threshold:
        return "PRESENT"
    if ambiguous:
        return "NEEDS_REVIEW"
    if now < end:
        return "IN_PROGRESS"
    return "ABSENT"


def _student_sessions(db: Session, student_id: int, day: date) -> list[AttendanceSession]:
    return list(
        db.scalars(
            select(AttendanceSession)
            .where(
                AttendanceSession.student_id == student_id,
                AttendanceSession.date == day,
            )
            .order_by(AttendanceSession.entry_time.asc())
        ).all()
    )


def compute_record(
    db: Session, period: Period, student: Student, day: date
) -> tuple[AttendanceRecord, list[AttendanceSession]]:
    """Calculate (but do not persist) one student's record + their intervals
    clipped to the period window."""
    start, end = period_window(period, day)
    now = utcnow()
    sessions = _student_sessions(db, student.id, day)
    intervals = clipped_intervals(sessions, start, end, now)
    present = sum(int((e - s).total_seconds()) for s, e in intervals)
    window_len = int((end - start).total_seconds())
    thr = threshold_seconds(period, window_len)
    ambiguous = _needs_review(db, student.id, start, end)
    machine = compute_machine_status(
        present_seconds=present,
        threshold=thr,
        start=start,
        end=end,
        now=now,
        ambiguous=ambiguous,
    )
    return (
        AttendanceRecord(
            period_id=period.id,
            student_id=student.id,
            date=day,
            presence_seconds=present,
            threshold_seconds=thr,
            machine_status=machine,
            computed_at=now,
        ),
        sessions,
    )


def _period_students(db: Session, period: Period) -> list[Student]:
    students = list(db.scalars(select(Student).order_by(Student.id.asc())).all())
    if period.section is not None:
        students = [s for s in students if s.section == period.section]
    return students


def _last_override(db: Session, record_id: int) -> AttendanceOverride | None:
    return db.scalars(
        select(AttendanceOverride)
        .where(AttendanceOverride.record_id == record_id)
        .order_by(AttendanceOverride.id.desc())
        .limit(1)
    ).first()


def recompute_period(
    db: Session, period: Period, day: date
) -> list[AttendanceRecord]:
    """Upsert every student's record for this period/day.

    Machine status and presence numbers are refreshed; an existing override
    chain is preserved (effective status never silently reverts).
    """
    now = utcnow()
    records: list[AttendanceRecord] = []
    for student in _period_students(db, period):
        computed, _sessions = compute_record(db, period, student, day)
        existing = db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.period_id == period.id,
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.date == day,
            )
        ).first()
        if existing is None:
            existing = computed
            existing.effective_status = (
                computed.machine_status
                if computed.machine_status in ("PRESENT", "ABSENT")
                else None
            )
            db.add(existing)
            db.flush()
        else:
            existing.presence_seconds = computed.presence_seconds
            existing.threshold_seconds = computed.threshold_seconds
            existing.machine_status = computed.machine_status
            existing.computed_at = now
            if existing.effective_status is None and computed.machine_status in (
                "PRESENT",
                "ABSENT",
            ):
                existing.effective_status = computed.machine_status
            db.flush()
        records.append(existing)
    return records


def apply_override(
    db: Session,
    record: AttendanceRecord,
    *,
    new_status: str,
    reason: str,
    actor_username: str,
    actor_role: str,
    actor_user_id: int | None,
) -> AttendanceOverride:
    """Record an audited manual correction; machine status is untouched."""
    previous = record.effective_status
    override = AttendanceOverride(
        record_id=record.id,
        period_id=record.period_id,
        student_id=record.student_id,
        date=record.date,
        previous_effective=previous,
        new_effective=new_status,
        reason=reason.strip(),
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        actor_role=actor_role,
    )
    db.add(override)
    record.effective_status = new_status
    db.flush()
    logger.info(
        "attendance override applied",
        extra={
            "record_id": record.id,
            "student_id": record.student_id,
            "period_id": record.period_id,
            "from": previous,
            "to": new_status,
            "actor": actor_username,
        },
    )
    return override
