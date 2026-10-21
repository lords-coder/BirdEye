# 19 — Corridor and Entry/Exit Regression Tests

**Sprint:** 2026-10-18  
**Problem:** P0-2 — unverified corridor-passersby handling

---

## Background

The evaluator asked: *"Will a person walking past in the corridor count
as being inside the classroom?"*

The presence state machine in `app/services/presence.py` was designed to
handle this correctly:

| Camera Role | Evidence Type | Effect |
|-------------|--------------|--------|
| `classroom_interior` | Entry | Creates/continues INSIDE state |
| `doorway_exterior` | Exit | Closes session / creates review event |
| Both / Neither | Bidirectional | Exit wins when INSIDE |

A corridor sighting (exterior camera, no open session) must produce a
**review event** (`NEEDS_REVIEW`), not an INSIDE state or an attendance
record.

However, there were **no regression tests** covering this scenario, so the
behaviour was unverified.

---

## New Tests

### `test_corridor_only_creates_review_not_inside`

**Setup:**
- Student `STU-COR1` (no session).
- Camera `Corridor` with `type="exit"`, `camera_role="doorway_exterior"`.

**Action:**
- `ps.observe(db, student_id=s.id, camera=exterior, ts=now)`

**Assertions:**
1. `out.review_event is True` — a review event was triggered.
2. `PresenceState.state != "INSIDE"` — student is NOT inside.
3. `AttendanceSession.count() == 0` — no attendance session invented.
4. `PresenceEvent.review_status == "open"` — review event is pending
   human resolution.

### `test_interior_creates_inside`

**Setup:**
- Student `STU-INT1` (no session).
- Camera `Interior` with `type="entry"`, `camera_role="classroom_interior"`.

**Action:**
- `ps.observe(db, student_id=s.id, camera=interior, ts=now)`

**Assertions:**
1. `out.state == "INSIDE"` — student is inside.
2. `PresenceState.state == "INSIDE"` — state machine transitioned.

---

## Existing Coverage (verified, not modified)

These tests in `tests/test_camera_attendance.py` already cover the full
entry/exit lifecycle and were confirmed passing:

| Test | Scenario |
|------|----------|
| `test_presence_entry_exit_lifecycle` | Interior → entry, exterior → exit, duration calculated |
| `test_presence_exit_without_session_flagged_not_invented` | Exterior-only sighting → review event, no session |
| `test_presence_pending_levels_need_two_observations` | EXIT_PENDING requires 2 observations (configurable) |
| `test_presence_exit_pending_cancelled_by_return` | Return to interior cancels pending exit |
| `test_presence_multi_camera_single_session` | Multiple cameras → one session |
| `test_presence_concurrent_observers_single_session` | Thread-safe session creation |

---

## Acceptance Scenario Coverage

| Scenario | Test | Status |
|----------|------|--------|
| Student enters via interior camera | `test_interior_creates_inside` | ✅ |
| Student exits via exterior camera | `test_presence_entry_exit_lifecycle` | ✅ |
| Corridor passerby (no session) | `test_corridor_only_creates_review_not_inside` | ✅ |
| Exit without session → review | `test_presence_exit_without_session_flagged_not_invented` | ✅ |
| Re-entry after exit | `test_presence_entry_exit_lifecycle` | ✅ |
| Multiple cameras, one session | `test_presence_multi_camera_single_session` | ✅ |
| Pending exit cancelled by return | `test_presence_exit_pending_cancelled_by_return` | ✅ |

---

## Key Invariant

**A corridor passerby NEVER counts as inside and NEVER gets an attendance
record.** The system creates a `PresenceEvent` with
`review_status="open"` for human review, and the student's `PresenceState`
remains `OUTSIDE` (or transitions to `NEEDS_REVIEW` if the state machine
has seen them before).

This is enforced by the `provides_entry_evidence()` check in
`presence.py`, which only returns `True` for cameras with
`camera_role="classroom_interior"`.
