"""Attendance query endpoints: per-student, daily summary, live view, audit
log, entry/exit history, per-period records and audited teacher overrides."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false as sa_false
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AttendanceOverride,
    AttendanceRecord,
    AttendanceSession,
    Camera,
    Course,
    Period,
    PresenceEvent,
    PresenceState,
    RecognitionLog,
    Student,
)
from ..schemas import (
    AttendanceRecordOut,
    AttendanceRecordsOut,
    AttendanceSessionOut,
    HistoryOut,
    HistoryRow,
    LiveOut,
    OverrideCreate,
    OverrideOut,
    PresenceEventOut,
    PresenceReviewRequest,
    PresenceStateOut,
    RecognitionLogOut,
    SessionTotals,
    StudentAttendanceOut,
    StudentSummary,
    SummaryOut,
)
from ..security import Principal, get_current_user
from ..services import attendance as svc
from ..services import attendance_engine as engine
from ..services import presence as presence_service
from ..timeutil import utcnow
from .courses import require_period_access, teacher_course_ids
from .deps import get_current_admin, get_db

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.post("/sessions/{session_id}/close", response_model=AttendanceSessionOut,
             summary="Force-close an ongoing attendance session")
def close_session(
    session_id: int,
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AttendanceSessionOut:
    from ..models import AttendanceSession as _AS
    session = db.get(_AS, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    if session.status == "completed":
        # already closed — return current representation
        camera_names = svc._camera_name_map(db)  # type: ignore[attr-defined]
        period_names = svc._period_name_map(db)  # type: ignore[attr-defined]
        return svc._session_out(session, camera_names, period_names)  # type: ignore[attr-defined]
    svc._close_session(db, session, utcnow(), camera_id=None)
    db.commit()
    db.refresh(session)
    camera_names = svc._camera_name_map(db)  # type: ignore[attr-defined]
    period_names = svc._period_name_map(db)  # type: ignore[attr-defined]
    return svc._session_out(session, camera_names, period_names)  # type: ignore[attr-defined]


def _parse_day(value: str | None) -> date:
    if value is None:
        return svc.local_date()  # type: ignore[attr-defined]
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from exc


@router.get("/summary", response_model=SummaryOut,
            summary="Daily per-student totals (entries/exits/time present)")
def attendance_summary(
    date_str: str | None = Query(default=None, alias="date",
                                 description="YYYY-MM-DD (defaults to today, local TZ)"),
    include_absent: bool = Query(default=False,
                                 description="Include students with zero sessions"),
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SummaryOut:
    day = _parse_day(date_str)
    return svc.summary_for_date(db, day, include_absent=include_absent)


@router.get("/live", response_model=LiveOut,
            summary="All ongoing sessions right now")
def attendance_live(
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> LiveOut:
    sessions = svc.live_sessions(db)
    return LiveOut(server_time=utcnow(), count=len(sessions), sessions=sessions)


@router.get("/logs", response_model=dict,
            summary="Raw recognition audit log (with GPU inference latency)")
def recognition_logs(
    student_id: int | None = Query(default=None),
    camera_id: int | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    since_id: int | None = Query(
        default=None, ge=0,
        description="Return only rows newer than this recognition_logs.id, "
                    "oldest-first (incremental cursor fetch for the live "
                    "console's fallback poll / reconnect reconciliation).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(RecognitionLog)
    if student_id is not None:
        stmt = stmt.where(RecognitionLog.student_id == student_id)
    if camera_id is not None:
        stmt = stmt.where(RecognitionLog.camera_id == camera_id)
    if start is not None:
        stmt = stmt.where(RecognitionLog.timestamp >= start)
    if end is not None:
        stmt = stmt.where(RecognitionLog.timestamp <= end)

    # Total matching rows: the web console paginates on `total` (this was
    # previously omitted, so page counts were computed from `undefined`).
    # Counted WITHOUT the cursor window, so "N of M" keeps meaning the whole
    # filtered table while the console live-follows new rows.
    total = (
        db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )

    if since_id is not None:
        # Cursor window: rows the caller has not seen yet.  Ascending by id
        # (the primary key is monotonic in write order) so the client can
        # advance its cursor to the LAST item and never skip a row, unlike
        # timestamp ordering which can tie.
        rows = db.scalars(
            stmt.where(RecognitionLog.id > since_id)
            .order_by(RecognitionLog.id.asc())
            .limit(limit)
        ).all()
    else:
        rows = db.scalars(
            stmt.order_by(RecognitionLog.timestamp.desc()).offset(offset).limit(limit)
        ).all()

    # Newest row id overall: lets the client close a reconciliation gap in
    # one round trip (cheap - max() on the primary key).
    newest_id = int(db.scalar(select(func.max(RecognitionLog.id))) or 0)
    if since_id is not None:
        # Conservative: only when rows came back (an empty page means the
        # caller is up to date, even if non-matching rows exist beyond it).
        has_more = bool(rows) and int(rows[-1].id) < newest_id
    else:
        has_more = (offset + len(rows)) < total

    student_names = {
        int(s.id): s.name
        for s in db.scalars(
            select(Student).where(Student.id.in_([r.student_id for r in rows if r.student_id]))
        ).all()
    } if rows else {}
    camera_names = {
        int(c.id): c.name
        for c in db.scalars(
            select(Camera).where(Camera.id.in_([r.camera_id for r in rows if r.camera_id]))
        ).all()
    } if rows else {}

    items = [
        RecognitionLogOut(
            id=r.id,
            student_id=r.student_id,
            student_name=student_names.get(r.student_id or -1),
            camera_id=r.camera_id,
            camera_name=camera_names.get(r.camera_id or -1),
            timestamp=r.timestamp,
            confidence_score=r.confidence_score,
            gpu_inference_time_ms=r.gpu_inference_time_ms,
        )
        for r in rows
    ]
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "newest_id": newest_id,
        "has_more": has_more,
    }


# -------------------------------------------- per-period records (engine)
# NOTE: registered BEFORE /period/{period_id}/{student_id} below - FastAPI
# matches routes in registration order, and the parameterized student route
# would otherwise swallow "/period/1/records" (student_id="records").
def _record_out(
    db: Session, record: AttendanceRecord, period: Period
) -> AttendanceRecordOut:
    student = db.get(Student, record.student_id)
    override = engine._last_override(db, record.id)
    day = record.date
    sessions = engine._student_sessions(db, record.student_id, day)
    camera_names = svc._camera_name_map(db)
    interval_outs = [
        svc._session_out(s, camera_names, {period.id: period.name})
        for s in sessions
    ]
    return AttendanceRecordOut(
        id=record.id,
        period_id=record.period_id,
        period_name=period.name,
        student=StudentSummary(
            id=student.id if student else record.student_id,
            name=student.name if student else f"#{record.student_id}",
            registration_no=student.registration_no if student else "",
            section=student.section if student else None,
        ),
        date=record.date,
        presence_seconds=record.presence_seconds,
        threshold_seconds=record.threshold_seconds,
        machine_status=record.machine_status,
        effective_status=record.effective_status,  # type: ignore[arg-type]
        overridden=override is not None,
        override_reason=override.reason if override else None,
        overridden_by=override.actor_username if override else None,
        overridden_at=override.created_at if override else None,
        intervals=interval_outs,
        computed_at=record.computed_at,
    )


@router.get(
    "/period/{period_id}/records",
    response_model=AttendanceRecordsOut,
    summary="Per-period attendance table (recomputed from presence intervals)",
)
def period_records(
    period_id: int,
    date_str: str | None = Query(default=None, alias="date"),
    principal: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttendanceRecordsOut:
    """Recompute + persist every student's record for this period/day and
    return the table (teacher-scoped)."""
    period = db.get(Period, period_id)
    if period is None:
        raise HTTPException(status_code=404, detail=f"period {period_id} not found")
    require_period_access(db, principal, period)
    day = _parse_day(date_str)
    records = engine.recompute_period(db, period, day)
    db.commit()
    start, end = engine.period_window(period, day)
    now = utcnow()
    return AttendanceRecordsOut(
        period_id=period.id,
        period_name=period.name,
        date=day,
        window_start=start,
        window_end=end,
        threshold_seconds=engine.threshold_seconds(
            period, int((end - start).total_seconds())
        ),
        is_past=now >= end,
        is_running=start <= now < end,
        records=[_record_out(db, r, period) for r in records],
    )


@router.post(
    "/period/{period_id}/compute",
    response_model=AttendanceRecordsOut,
    summary="Force recomputation of a period's attendance records",
)
def compute_period(
    period_id: int,
    date_str: str | None = Query(default=None, alias="date"),
    principal: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttendanceRecordsOut:
    return period_records(period_id, date_str, principal, db)


@router.get("/period/{period_id}/summary", response_model=SummaryOut,
            summary="Per-period attendance summary")
def period_summary(
    period_id: int,
    date_str: str | None = Query(default=None, alias="date",
                                 description="YYYY-MM-DD (defaults to today, local TZ)"),
    include_absent: bool = Query(default=False,
                                 description="Include students with zero sessions in this period"),
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SummaryOut:
    try:
        return svc.period_summary(db, period_id, _parse_day(date_str), include_absent=include_absent)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/period/{period_id}/{student_id}", response_model=StudentAttendanceOut,
            summary="Per-period attendance for one student")
def period_student_attendance(
    period_id: int,
    student_id: int,
    date_str: str | None = Query(default=None, alias="date",
                                 description="YYYY-MM-DD (defaults to today)"),
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> StudentAttendanceOut:
    from ..models import Period as _Period

    if db.get(_Period, period_id) is None:
        raise HTTPException(status_code=404, detail=f"period {period_id} not found")
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail=f"student {student_id} not found")
    return svc.period_student_attendance(db, period_id, student, _parse_day(date_str))


# ------------------------------------------------------ entry/exit history
def _teacher_sections(db: Session, principal: Principal) -> set[str] | None:
    """Sections a teacher may view; ``None`` = unrestricted (admin),
    empty set = nothing (teacher without assigned sections)."""
    if principal.is_admin:
        return None
    ids = teacher_course_ids(db, principal)
    sections: set[str] = set()
    for cid in ids:
        course = db.get(Course, cid)
        if course is not None and course.section:
            sections.add(course.section)
    return sections


@router.get(
    "/history",
    response_model=HistoryOut,
    summary="Student entry/exit interval history with filters",
)
def attendance_history(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    student_id: int | None = Query(default=None),
    camera_id: int | None = Query(default=None, description="Entry or exit camera"),
    section: str | None = Query(default=None),
    period_id: int | None = Query(default=None),
    entry_after: datetime | None = Query(default=None),
    exit_before: datetime | None = Query(default=None),
    unresolved: bool = Query(default=False, description="Only open (no observed exit) sessions"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoryOut:
    """Every confirmed presence interval with full context (filters are AND
    -combined).  Teachers only see students in their assigned course
    sections; admins see everything."""
    stmt = select(AttendanceSession).join(
        Student, Student.id == AttendanceSession.student_id
    )
    if date_from is not None:
        stmt = stmt.where(AttendanceSession.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(AttendanceSession.date <= date_to)
    if student_id is not None:
        stmt = stmt.where(AttendanceSession.student_id == student_id)
    if camera_id is not None:
        stmt = stmt.where(
            (AttendanceSession.camera_in_id == camera_id)
            | (AttendanceSession.camera_out_id == camera_id)
        )
    if period_id is not None:
        stmt = stmt.where(AttendanceSession.period_id == period_id)
    if section is not None:
        stmt = stmt.where(Student.section == section)
    scoped = _teacher_sections(db, principal)
    if scoped is not None:
        if scoped:
            stmt = stmt.where(Student.section.in_(scoped))
        else:
            stmt = stmt.where(sa_false())
    if entry_after is not None:
        stmt = stmt.where(AttendanceSession.entry_time >= entry_after)
    if exit_before is not None:
        stmt = stmt.where(AttendanceSession.exit_time <= exit_before)
    if unresolved:
        stmt = stmt.where(AttendanceSession.status == "ongoing")

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AttendanceSession.entry_time.desc()).offset(offset).limit(limit)
    ).all()

    students = {
        int(s.id): s
        for s in db.scalars(
            select(Student).where(
                Student.id.in_({r.student_id for r in rows})
            )
        ).all()
    }
    cameras = {
        int(c.id): c.name
        for c in db.scalars(
            select(Camera).where(
                Camera.id.in_(
                    {r.camera_in_id for r in rows if r.camera_in_id}
                    | {r.camera_out_id for r in rows if r.camera_out_id}
                )
            )
        ).all()
    }
    period_names = {
        int(p.id): p.name
        for p in db.scalars(
            select(Period).where(
                Period.id.in_({r.period_id for r in rows if r.period_id})
            )
        ).all()
    }
    now = utcnow()
    items: list[HistoryRow] = []
    for r in rows:
        student = students.get(r.student_id)
        if student is None:
            continue
        entry = svc._as_utc(r.entry_time)
        exit_t = svc._as_utc(r.exit_time) if r.exit_time else None
        duration = (
            int((exit_t - entry).total_seconds())
            if exit_t is not None
            else max(0, int((now - entry).total_seconds()))
        )
        items.append(
            HistoryRow(
                session_id=r.id,
                student=StudentSummary(
                    id=student.id,
                    name=student.name,
                    registration_no=student.registration_no,
                    section=student.section,
                ),
                entry_time=entry,
                exit_time=exit_t,
                duration_seconds=duration,
                date=r.date,
                status=r.status,  # type: ignore[arg-type]
                unresolved=(r.status == "ongoing"),
                review_status=r.review_status,
                source=r.source,
                camera_in_id=r.camera_in_id,
                camera_in_name=cameras.get(r.camera_in_id or -1),
                camera_out_id=r.camera_out_id,
                camera_out_name=cameras.get(r.camera_out_id or -1),
                period_id=r.period_id,
                period_name=period_names.get(r.period_id or -1),
            )
        )

    # Totals across the FILTERED set (not only the page).
    all_rows = db.scalars(stmt.order_by(AttendanceSession.entry_time.asc())).all()
    totals_seconds = sum(
        int(
            (
                (svc._as_utc(r.exit_time) if r.exit_time else now)
                - svc._as_utc(r.entry_time)
            ).total_seconds()
        )
        for r in all_rows
    )
    return HistoryOut(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
        totals=SessionTotals(
            entries=len(all_rows),
            exits=sum(1 for r in all_rows if r.status == "completed"),
            total_seconds=max(0, totals_seconds),
            first_entry=min(
                (svc._as_utc(r.entry_time) for r in all_rows), default=None
            ),
            last_exit=max(
                (svc._as_utc(r.exit_time) for r in all_rows if r.exit_time),
                default=None,
            ),
        ),
    )


# -------------------------------------------------------------- overrides
@router.post(
    "/records/{record_id}/override",
    response_model=OverrideOut,
    summary="Manual attendance correction (reason required, fully audited)",
)
def override_record(
    record_id: int,
    body: OverrideCreate,
    principal: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OverrideOut:
    record = db.get(AttendanceRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")
    period = db.get(Period, record.period_id)
    if period is None:
        raise HTTPException(status_code=404, detail="period missing for record")
    require_period_access(db, principal, period)

    reason = body.reason.strip()
    if len(reason) < settings.override_reason_min_length:
        raise HTTPException(
            status_code=422,
            detail=(
                f"reason must be at least {settings.override_reason_min_length} "
                "characters (after trimming)"
            ),
        )
    override = engine.apply_override(
        db,
        record,
        new_status=body.status,
        reason=reason,
        actor_username=principal.username,
        actor_role=principal.role,
        actor_user_id=principal.user_id,
    )
    db.commit()
    db.refresh(override)
    return OverrideOut.model_validate(override)


@router.get(
    "/records/{record_id}/overrides",
    response_model=list[OverrideOut],
    summary="Override audit history for one record",
)
def record_overrides(
    record_id: int,
    principal: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OverrideOut]:
    record = db.get(AttendanceRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")
    period = db.get(Period, record.period_id)
    if period is not None:
        require_period_access(db, principal, period)
    rows = db.scalars(
        select(AttendanceOverride)
        .where(AttendanceOverride.record_id == record_id)
        .order_by(AttendanceOverride.id.asc())
    ).all()
    return [OverrideOut.model_validate(r) for r in rows]


# ------------------------------------------------------------ presence view
@router.get(
    "/presence/states",
    response_model=list[PresenceStateOut],
    summary="Current presence state of every tracked student (admin)",
)
def presence_states(
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[PresenceStateOut]:
    rows = db.scalars(select(PresenceState).order_by(PresenceState.student_id.asc())).all()
    camera_names = svc._camera_name_map(db)
    out: list[PresenceStateOut] = []
    for st in rows:
        student = db.get(Student, st.student_id)
        out.append(
            PresenceStateOut(
                student_id=st.student_id,
                student_name=student.name if student else None,
                registration_no=student.registration_no if student else None,
                state=st.state,
                since=st.since,
                last_seen_at=st.last_seen_at,
                last_camera_id=st.last_camera_id,
                last_camera_name=camera_names.get(st.last_camera_id or -1),
                pending_observations=st.pending_observations,
                open_session_id=st.open_session_id,
                review_reason=st.review_reason,
            )
        )
    return out


@router.get(
    "/presence/events",
    response_model=list[PresenceEventOut],
    summary="Presence transition audit trail (admin)",
)
def presence_events(
    student_id: int | None = Query(default=None),
    open_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[PresenceEventOut]:
    rows = presence_service.list_review_events(
        db, student_id=student_id, open_only=open_only, limit=limit
    )
    camera_names = svc._camera_name_map(db)
    out: list[PresenceEventOut] = []
    for ev in rows:
        student = db.get(Student, ev.student_id)
        out.append(
            PresenceEventOut(
                id=ev.id,
                student_id=ev.student_id,
                student_name=student.name if student else None,
                from_state=ev.from_state,
                to_state=ev.to_state,
                camera_id=ev.camera_id,
                camera_name=camera_names.get(ev.camera_id or -1),
                event_time=ev.event_time,
                confidence=ev.confidence,
                session_id=ev.session_id,
                reason=ev.reason,
                review_status=ev.review_status,
                reviewed_by=ev.reviewed_by,
                reviewed_at=ev.reviewed_at,
            )
        )
    return out


@router.post(
    "/presence/events/{event_id}/review",
    response_model=PresenceEventOut,
    summary="Resolve a presence review event (admin)",
)
def review_presence_event(
    event_id: int,
    body: PresenceReviewRequest,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> PresenceEventOut:
    event = db.get(PresenceEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    event = presence_service.resolve_review(
        db, event, action=body.action, actor=admin, note=body.note
    )
    db.commit()
    db.refresh(event)
    return PresenceEventOut(
        id=event.id,
        student_id=event.student_id,
        from_state=event.from_state,
        to_state=event.to_state,
        camera_id=event.camera_id,
        event_time=event.event_time,
        confidence=event.confidence,
        session_id=event.session_id,
        reason=event.reason,
        review_status=event.review_status,
        reviewed_by=event.reviewed_by,
        reviewed_at=event.reviewed_at,
    )


@router.get("/{student_id}", response_model=StudentAttendanceOut,
            summary="Attendance for one student on a given date")
def student_attendance(
    student_id: int,
    date_str: str | None = Query(default=None, alias="date",
                                 description="YYYY-MM-DD (defaults to today)"),
    _: str = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> StudentAttendanceOut:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail=f"student {student_id} not found")
    return svc.student_attendance(db, student, _parse_day(date_str))
