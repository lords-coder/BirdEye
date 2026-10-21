# Entry/Exit Tracking (02)

## State machine (`app/services/presence.py`)

One `presence_states` row per student:
`OUTSIDE → ENTRY_PENDING → INSIDE → EXIT_PENDING → OUTSIDE`, plus
`NEEDS_REVIEW` for inconsistent evidence. Every confirmed transition
appends a `presence_events` row (from/to, camera, **frame timestamp**,
confidence, linked session, reason, review status).

## Evidence rules

- **Interior evidence** (`camera_role=classroom_interior`; legacy rows:
  type entry/both) is the only thing that moves a student INTO the room.
- **Exterior evidence** (`doorway_exterior`; legacy: type exit/both) starts
  `EXIT_PENDING` for an INSIDE student; for an OUTSIDE student it raises an
  open `NEEDS_REVIEW` event ("exit sighted without an active session",
  deduped by `PRESENCE_REVIEW_DEDUP_S`) — never an invented exit. An alert
  (`exit_without_session`) is emitted.
- Legacy `both` cameras keep exit-when-open semantics (D-11).
- Confirmation: the camera-level K-of-N gate (3 frames/10 s) still vets
  every sighting; `PRESENCE_TRANSITION_OBSERVATIONS` (default 1, D-9) sets
  how many confirmed sightings cross a pending level.

## Session layer (unchanged)

Open/close still delegates to `app.services.attendance`
(`process_recognition`), preserving entry/exit/stale/midnight rules.
Closes use the sighting's event time, never worker processing time.
Open sessions with no observed exit keep `review_status` open and are
reported as unresolved — exits are never fabricated.

## Concurrency & resilience

- State row is `SELECT … FOR UPDATE`; creation races are catch-and-reuse
  (bug found by test, fixed).
- Idempotent: repeat sightings inside a state are no-ops; two workers on
  one student converge on a single session (thread test).
- Camera disconnects leave state/sessions intact; stale previous-day
  sessions close only on the next real sighting, audited as
  `stale_closed_and_entry`.

## Operator surface

- `GET /attendance/presence/states`, `GET /attendance/presence/events`
  (open/all filters), `POST /attendance/presence/events/{id}/review`
  (confirm/dismiss, admin) — plus the `presence_state` live-feed event.
- `GET /attendance/history` — interval history with from/to, student,
  camera, section, period, entry/exit bounds, open-only filters and totals;
  teachers are scoped to their course sections.
