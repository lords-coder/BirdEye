# 20 — Camera Independence Tests

**Sprint:** 2026-10-18  
**Problem:** P0-1 — camera processing coupled to frontend page

---

## Design Principle

Both cameras must operate **simultaneously and independently** of which
page the admin has open. Pipeline state is tracked in Redis (not in
browser memory), and each camera has its own pipeline process and slot.

---

## Architecture (verified)

| Component | Scope | Isolation |
|-----------|-------|-----------|
| Pipeline process | Per camera | Separate Celery task + slot |
| Redis state keys | Per camera | `cam:{id}:state`, `cam:{id}:hb`, `cam:{id}:slot` |
| Frame publication | Per camera | `cam:{id}:frame` (binary), `cam:{id}:live` (hash) |
| Snapshot endpoint | Per camera | Reads from Redis when pipeline running |
| Live monitor | Per camera | Reads from Redis, header auth |

---

## New Tests

### `test_two_pipelines_independent`

**Setup:**
- Two cameras: `Cam A` (host 192.0.2.10), `Cam B` (host 192.0.2.11).
- `fake_celery` autouse fixture stubs `send_task` and `revoke`.

**Actions:**
1. `POST /cameras/{A}/start` → state "starting", slot 0.
2. `touch_heartbeat(A)` → marks heartbeat fresh.
3. `POST /cameras/{B}/start` → state "starting", slot 1.
4. `touch_heartbeat(B)` → marks heartbeat fresh.
5. `POST /cameras/{A}/stop` → state "stopping".

**Assertions:**
- After stopping A: A is "stopping", B is still "starting".
- B's pipeline state is unaffected by A stopping.

### `test_snapshot_while_other_camera_running`

**Setup:**
- Camera A (pipeline stopped), Camera B (pipeline running with seeded frame).

**Action:**
- `GET /cameras/{A}/snapshot` (with mocked probe to avoid 30 s timeout).

**Assertions:**
- Camera B is still running after A's snapshot request.

---

## Existing Coverage (verified, not modified)

| Test | File | Scenario |
|------|------|----------|
| `test_camera_start_stop_and_slot_limits` | `test_api.py` | 2 slots, 3rd camera gets 409 |
| `test_live_pipeline_publishes_frames` | `test_live_monitor.py` | Pipeline publishes to Redis |
| `test_camera_pipeline_uses_shared_frame_bus` | `test_live_monitor.py` | Frame bus decoupled from frontend |

---

## How Independence Is Achieved

### 1. Redis-Backed State
Pipeline state (`cam:{id}:state`, heartbeat, slot) lives in Redis, not in
browser memory. The frontend reads state via `GET /cameras/{id}/pipeline/status`;
it never controls the pipeline directly.

### 2. Per-Camera Celery Tasks
Each camera's pipeline is a separate Celery task (`camera.run`) on a
per-slot queue. Stopping camera A revokes only A's task.

### 3. Shared Frame Bus
The pipeline publishes annotated frames to `cam:{id}:frame` (binary key).
All consumers (snapshot, live monitor, legacy stream) read from this key.
No consumer opens RTSP connections while the pipeline is running.

### 4. Snapshot Fix (this sprint)
The snapshot endpoint now reads from Redis when the pipeline is running,
eliminating the competing RTSP capture that caused page-dependent
processing.

---

## Acceptance Checklist

| Requirement | Status |
|-------------|--------|
| Both cameras operate simultaneously | ✅ Slots 0 and 1, independent tasks |
| Independent of which page admin views | ✅ Redis-backed state, no RTSP from API |
| Stopping one camera doesn't affect the other | ✅ Verified by test |
| Snapshot for one camera doesn't affect the other | ✅ Verified by test |
| Pipeline survives frontend disconnect | ✅ Redis state, Celery task |
| Pipeline survives Redis restart | ✅ State restored from DB on startup |
