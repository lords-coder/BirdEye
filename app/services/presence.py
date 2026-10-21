"""Persistent per-student classroom presence state machine.

States (one :class:`PresenceState` row per student):

``OUTSIDE``         no confirmed presence
``ENTRY_PENDING``   interior-camera sightings accumulating toward entry
``INSIDE``          confirmed presence; an open attendance session exists
``EXIT_PENDING``    exit evidence accumulating toward leaving
``NEEDS_REVIEW``    inconsistent evidence (e.g. an exit sighted with no
                    active session) awaiting a human decision

Evidence rules
--------------
* **Interior evidence** - a camera whose ``camera_role`` is
  ``classroom_interior`` (legacy rows: ``type`` entry/both).  A confirmed
  sighting here is the only thing that can move a student INTO the room.
* **Exit evidence** - a ``doorway_exterior`` camera (legacy rows: type
  exit/both).  A sighting on the exterior camera ALONE never proves a
  student left: for a student in ``OUTSIDE`` it raises a reviewable
  ``exit_without_session`` event instead of inventing a transition.  For a
  student who is ``INSIDE`` it starts ``EXIT_PENDING``; the transition
  completes only with enough consistent observations
  (``PRESENCE_TRANSITION_OBSERVATIONS``), each of which has already passed
  the camera-level K-of-N gate (``CONFIRMATION_FRAMES`` /
  ``CONFIRMATION_WINDOW_S``).

Timing
------
All transitions use the observation (frame) timestamp - never the worker's
processing time.  Session open/close is delegated to
:mod:`app.services.attendance`, whose entry/exit semantics and midnight
rollover rules are unchanged; this module adds the persistent state layer,
the review path and the audit trail on top.

Concurrency
-----------
The state row is selected ``FOR UPDATE`` inside the observation
transaction, so two workers processing the same student serialize instead
of opening duplicate sessions (PostgreSQL; SQLite serializes writes
anyway).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AttendanceSession,
    Camera,
    PresenceEvent,
    PresenceState,
)
from .attendance import process_recognition

logger = logging.getLogger("app.presence")

OUTSIDE = "OUTSIDE"
ENTRY_PENDING = "ENTRY_PENDING"
INSIDE = "INSIDE"
EXIT_PENDING = "EXIT_PENDING"
NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class PresenceOutcome:
    """Result of one observation for one student."""

    state: str  # state after processing
    transitioned: bool = False
    action: str = "none"  # session action (entry_created | exit_completed | ...)
    session: AttendanceSession | None = None
    review_event: bool = False  # a NEEDS_REVIEW event was raised
    from_state: str | None = None

    @property
    def changed(self) -> bool:
        """True when the observation produced any persisted effect."""
        return self.transitioned or self.review_event or self.action != "none"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ------------------------------------------------------------------ evidence
def provides_entry_evidence(camera: Camera) -> bool:
    """Interior view: the only valid evidence a student entered."""
    if camera.camera_role == "classroom_interior":
        return True
    if camera.camera_role is None:  # legacy rows: type is the purpose
        return camera.type in ("entry", "both")
    return False


def provides_exit_evidence(camera: Camera) -> bool:
    """Doorway/exterior view: evidence a student is leaving/has left."""
    if camera.camera_role == "doorway_exterior":
        return True
    if camera.camera_role is None:
        return camera.type in ("exit", "both")
    return False


def _needed_observations() -> int:
    return max(1, int(settings.presence_transition_observations))


# ---------------------------------------------------------------- state row
def get_state(db: Session, student_id: int, *, for_update: bool = False) -> PresenceState | None:
    stmt = select(PresenceState).where(PresenceState.student_id == student_id)
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def get_or_create_state(db: Session, student_id: int, *, for_update: bool = False) -> PresenceState:
    state = get_state(db, student_id, for_update=for_update)
    if state is not None:
        return state
    state = PresenceState(student_id=student_id, state=OUTSIDE, pending_observations=0)
    db.add(state)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with another worker observing the same student: the
        # row exists now - roll back and use it instead of failing.
        db.rollback()
        state = get_state(db, student_id, for_update=for_update)
        if state is None:  # pragma: no cover - defensive; the row was just created
            raise
        return state
    if for_update:  # re-select with the lock now that the row exists
        locked = db.scalars(
            select(PresenceState)
            .where(PresenceState.student_id == student_id)
            .with_for_update()
        ).first()
        if locked is not None:
            return locked
    return state


def _record_event(
    db: Session,
    *,
    student_id: int,
    from_state: str | None,
    to_state: str,
    camera: Camera | None,
    event_time: datetime,
    confidence: float | None,
    session: AttendanceSession | None,
    reason: str | None,
    review_status: str = "confirmed",
) -> PresenceEvent:
    event = PresenceEvent(
        student_id=student_id,
        from_state=from_state,
        to_state=to_state,
        camera_id=camera.id if camera is not None else None,
        event_time=event_time,
        confidence=confidence,
        session_id=session.id if session is not None else None,
        reason=reason,
        review_status=review_status,
    )
    db.add(event)
    db.flush()
    return event


def _transition(
    db: Session,
    state: PresenceState,
    *,
    to_state: str,
    camera: Camera | None,
    event_time: datetime,
    confidence: float | None,
    session: AttendanceSession | None = None,
    reason: str | None = None,
    review_status: str = "confirmed",
) -> None:
    _record_event(
        db,
        student_id=state.student_id,
        from_state=state.state,
        to_state=to_state,
        camera=camera,
        event_time=event_time,
        confidence=confidence,
        session=session,
        reason=reason,
        review_status=review_status,
    )
    state.state = to_state
    state.since = event_time
    state.pending_observations = 0
    state.pending_since = None
    # Only states that own an OPEN session keep the pointer; an exit
    # transition receives the just-CLOSED session for the audit link and
    # must not store it as open.
    if to_state in (INSIDE, EXIT_PENDING):
        state.open_session_id = session.id if session is not None else None
    else:
        state.open_session_id = None


def _recent_open_review(db: Session, student_id: int, event_time: datetime) -> bool:
    """An open review event for this student inside the dedup window?"""
    window = int(settings.presence_review_dedup_s)
    if window <= 0:
        return False
    cutoff = event_time - timedelta(seconds=window)
    row = db.scalars(
        select(PresenceEvent)
        .where(
            PresenceEvent.student_id == student_id,
            PresenceEvent.review_status == "open",
            PresenceEvent.event_time >= cutoff,
        )
        .limit(1)
    ).first()
    return row is not None


# -------------------------------------------------------------- observation
def observe(
    db: Session,
    *,
    student_id: int,
    camera: Camera,
    ts: datetime,
    confidence: float | None = None,
) -> PresenceOutcome:
    """Apply one camera-confirmed sighting to the student's state machine.

    The caller (camera pipeline) has already passed the K-of-N frame gate;
    duplicate frames inside the dedup window never reach here.
    """
    ts = _as_utc(ts)
    state = get_or_create_state(db, student_id, for_update=True)
    before = state.state
    entry_ev = provides_entry_evidence(camera)
    exit_ev = provides_exit_evidence(camera)
    needed = _needed_observations()
    outcome = PresenceOutcome(state=before)

    # ---------------------------------------------------------------- INSIDE
    if before == INSIDE:
        # Exit evidence wins over entry evidence here: a legacy 'both'
        # camera closes an open session when it sees the student (the
        # session layer's long-standing rule), while an interior-only
        # camera is a no-op (plus stale-session handling below).
        if exit_ev:
            # Doorway evidence while inside -> EXIT_PENDING (or direct close
            # when a single observation is configured to confirm).
            if needed <= 1:
                recog = process_recognition(
                    db, student_id=student_id, camera=camera, ts=ts
                )
                _transition(
                    db, state, to_state=OUTSIDE, camera=camera, event_time=ts,
                    confidence=confidence, session=recog.session,
                    reason="exit confirmed at doorway camera",
                )
                outcome.transitioned = True
                outcome.action = recog.action
                outcome.session = recog.session
                outcome.state = OUTSIDE
                return _finish(db, state, outcome, before)
            _transition(
                db, state, to_state=EXIT_PENDING, camera=camera,
                event_time=ts, confidence=confidence,
                reason="doorway evidence accumulating",
            )
            # _transition resets the counters: start this pending level now.
            state.pending_observations = 1
            state.pending_since = ts
            state.last_seen_at = ts
            state.last_camera_id = camera.id
            outcome.transitioned = True
            outcome.state = EXIT_PENDING
            return _finish(db, state, outcome, before)
        if entry_ev:
            # Seen inside again: no state change, but let the session layer
            # apply its stale-session policy (previous-day leftover).
            recog = process_recognition(db, student_id=student_id, camera=camera, ts=ts)
            state.last_seen_at = ts
            state.last_camera_id = camera.id
            if recog.action == "stale_closed_and_entry":
                _transition(
                    db, state, to_state=INSIDE, camera=camera, event_time=ts,
                    confidence=confidence, session=recog.session,
                    reason="re-entered; stale session closed",
                )
                outcome.transitioned = True
                outcome.action = recog.action
                outcome.session = recog.session
            return _finish(db, state, outcome, before)
        return _finish(db, state, outcome, before)

    # ----------------------------------------------------------- EXIT_PENDING
    if before == EXIT_PENDING:
        if exit_ev:
            state.pending_observations += 1
            state.last_seen_at = ts
            state.last_camera_id = camera.id
            if state.pending_observations >= needed:
                recog = process_recognition(
                    db, student_id=student_id, camera=camera, ts=ts
                )
                _transition(
                    db, state, to_state=OUTSIDE, camera=camera, event_time=ts,
                    confidence=confidence, session=recog.session,
                    reason="exit confirmed",
                )
                outcome.transitioned = True
                outcome.action = recog.action
                outcome.session = recog.session
                outcome.state = OUTSIDE
            return _finish(db, state, outcome, before)
        if entry_ev:
            # Returned inside before the exit was confirmed: cancel.
            _transition(
                db, state, to_state=INSIDE, camera=camera, event_time=ts,
                confidence=confidence,
                reason="exit pending cancelled by interior sighting",
            )
            outcome.transitioned = True
            outcome.state = INSIDE
            return _finish(db, state, outcome, before)
        return _finish(db, state, outcome, before)

    # ----------------------------------------------------------- NEEDS_REVIEW
    if before == NEEDS_REVIEW:
        if entry_ev:
            # Interior sighting resolves the ambiguity: student is inside.
            recog = process_recognition(
                db, student_id=student_id, camera=camera, ts=ts
            )
            _transition(
                db, state, to_state=INSIDE, camera=camera, event_time=ts,
                confidence=confidence, session=recog.session,
                reason="review resolved by interior sighting",
            )
            outcome.transitioned = True
            outcome.action = recog.action
            outcome.session = recog.session
            outcome.state = INSIDE
        return _finish(db, state, outcome, before)

    # ------------------------------------------------------ OUTSIDE / PENDING
    if before == ENTRY_PENDING:
        if exit_ev and not entry_ev:
            _transition(
                db, state, to_state=OUTSIDE, camera=camera, event_time=ts,
                confidence=confidence,
                reason="entry pending cancelled by doorway sighting",
            )
            outcome.transitioned = True
            outcome.state = OUTSIDE
            return _finish(db, state, outcome, before)
        state.pending_observations += 1
        state.last_seen_at = ts
        state.last_camera_id = camera.id
        if state.pending_observations >= needed:
            recog = process_recognition(db, student_id=student_id, camera=camera, ts=ts)
            _transition(
                db, state, to_state=INSIDE, camera=camera, event_time=ts,
                confidence=confidence, session=recog.session,
                reason="entry confirmed by interior camera",
            )
            outcome.transitioned = True
            outcome.action = recog.action
            outcome.session = recog.session
            outcome.state = INSIDE
        return _finish(db, state, outcome, before)

    # before == OUTSIDE
    if entry_ev:
        if needed <= 1:
            recog = process_recognition(db, student_id=student_id, camera=camera, ts=ts)
            _transition(
                db, state, to_state=INSIDE, camera=camera, event_time=ts,
                confidence=confidence, session=recog.session,
                reason="entry confirmed by interior camera",
            )
            outcome.transitioned = True
            outcome.action = recog.action
            outcome.session = recog.session
            outcome.state = INSIDE
            return _finish(db, state, outcome, before)
        _transition(
            db, state, to_state=ENTRY_PENDING, camera=camera, event_time=ts,
            confidence=confidence,
            reason="interior evidence accumulating",
        )
        # _transition resets the counters: start this pending level now.
        state.pending_observations = 1
        state.pending_since = ts
        state.last_seen_at = ts
        state.last_camera_id = camera.id
        outcome.transitioned = True
        outcome.state = ENTRY_PENDING
        return _finish(db, state, outcome, before)

    if exit_ev:
        # Doorway sighting with NO active session: never invent an exit.
        # Flag for review (deduplicated) so the anomaly stays visible.
        if not _recent_open_review(db, student_id, ts):
            _record_event(
                db,
                student_id=student_id,
                from_state=OUTSIDE,
                to_state=NEEDS_REVIEW,
                camera=camera,
                event_time=ts,
                confidence=confidence,
                session=None,
                reason="exit sighted without an active session",
                review_status="open",
            )
            state.review_reason = "exit sighted without an active session"
            outcome.review_event = True
        state.last_seen_at = ts
        state.last_camera_id = camera.id
    return _finish(db, state, outcome, before)


def _finish(
    db: Session, state: PresenceState, outcome: PresenceOutcome, before: str
) -> PresenceOutcome:
    outcome.state = state.state
    outcome.from_state = before
    if outcome.transitioned:
        logger.info(
            "presence transition",
            extra={
                "student_id": state.student_id,
                "from": before,
                "to": state.state,
                "action": outcome.action,
            },
        )
    return outcome


# ------------------------------------------------------------------- review
def list_review_events(
    db: Session, *, student_id: int | None = None, open_only: bool = True,
    limit: int = 100,
) -> list[PresenceEvent]:
    stmt = select(PresenceEvent).order_by(PresenceEvent.event_time.desc())
    if open_only:
        stmt = stmt.where(PresenceEvent.review_status == "open")
    if student_id is not None:
        stmt = stmt.where(PresenceEvent.student_id == student_id)
    return list(db.scalars(stmt.limit(limit)).all())


def resolve_review(
    db: Session,
    event: PresenceEvent,
    *,
    action: str,
    actor: str,
    note: str | None = None,
) -> PresenceEvent:
    """Close an open review event ('confirm' | 'dismiss') and clear the
    student's review marker when nothing else is pending."""
    if event.review_status != "open":
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=f"event already {event.review_status}")
    event.review_status = "confirmed" if action == "confirm" else "dismissed"
    event.reviewed_by = actor
    event.reviewed_at = datetime.now(timezone.utc)
    if note:
        event.reason = f"{event.reason} | review: {note}"[:200]
    state = get_state(db, event.student_id)
    if state is not None and state.review_reason:
        remaining = db.scalars(
            select(PresenceEvent)
            .where(
                PresenceEvent.student_id == event.student_id,
                PresenceEvent.review_status == "open",
                PresenceEvent.id != event.id,
            )
            .limit(1)
        ).first()
        if remaining is None:
            state.review_reason = None
    db.flush()
    return event
