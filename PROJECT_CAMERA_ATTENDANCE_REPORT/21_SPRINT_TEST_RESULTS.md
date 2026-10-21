# 21 — Sprint Test Results

**Sprint:** 2026-10-18  
**Command:** `.venv\Scripts\python.exe -m pytest --tb=short`

---

## Summary

| Metric | Value |
|--------|-------|
| Baseline (before sprint) | 237 passed, 0 failed |
| After sprint fixes | **250 passed, 0 failed** |
| New tests added | 13 |
| Regressions | **0** |
| Execution time | 28.86 s |

---

## New Sprint Tests (`tests/test_sprint_fixes.py`)

### P0-3: Unknown-Face Dedup

| Test | Result | What it proves |
|------|--------|---------------|
| `test_same_unknown_jittered_frames_single_event` | ✅ | 20 jittered frames → 1 event, 1 log, 1 alert |
| `test_two_distinct_unknowns_get_separate_events` | ✅ | Two distinct faces → 2 separate events |
| `test_unknown_hash_quantized_stable` | ✅ | Hash stable across small perturbations |
| `test_unknown_hash_distinguishes_different_faces` | ✅ | Hash differs for different faces |

### P0-1/P0-4: Snapshot Redis Fix

| Test | Result | What it proves |
|------|--------|---------------|
| `test_snapshot_reads_redis_when_pipeline_running` | ✅ | Snapshot serves from Redis (JPEG magic verified) |
| `test_snapshot_direct_probe_when_pipeline_stopped` | ✅ | Direct probe used when pipeline stopped |
| `test_snapshot_never_calls_probe_when_running` | ✅ | Probe raises if called while running |
| `test_snapshot_while_other_camera_running` | ✅ | Snapshot A doesn't affect B |

### P0-2: Corridor Passersby

| Test | Result | What it proves |
|------|--------|---------------|
| `test_corridor_only_creates_review_not_inside` | ✅ | Corridor sighting → review event, no session |
| `test_interior_creates_inside` | ✅ | Interior sighting → INSIDE |

### P0-1: Camera Independence

| Test | Result | What it proves |
|------|--------|---------------|
| `test_two_pipelines_independent` | ✅ | Stop A doesn't affect B |

### Config Sanity

| Test | Result | What it proves |
|------|--------|---------------|
| `test_unknown_cooldown_config_exists` | ✅ | Config setting present |
| `test_dedup_window_config_exists` | ✅ | Config setting present |

---

## Full Suite Breakdown

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_api.py` | 28 | ✅ All passing |
| `test_camera_attendance.py` | 35 | ✅ All passing |
| `test_live_monitor.py` | 18 | ✅ All passing |
| `test_alerts.py` | 12 | ✅ All passing |
| `test_multisubject.py` | 8 | ✅ All passing |
| `test_pipeline_e2e.py` | 15 | ✅ All passing |
| `test_sprint_fixes.py` | **13** | ✅ **All passing** |
| Other test files | 121 | ✅ All passing |
| **Total** | **250** | ✅ **0 failed** |

---

## Performance

| Metric | Value |
|--------|-------|
| Test execution time | 28.86 s |
| Warning count | 1 (StarletteDeprecationWarning, pre-existing) |
| Coverage | Hardware-independent (synthetic embeddings, fakeredis) |

---

## Verification Commands

```bash
# Full suite
.venv\Scripts\python.exe -m pytest --tb=short

# Sprint tests only
.venv\Scripts\python.exe -m pytest tests/test_sprint_fixes.py -v --tb=short

# Specific test
.venv\Scripts\python.exe -m pytest tests/test_sprint_fixes.py::TestUnknownFaceDedup -v
```

---

## Key Invariants Verified

1. **Unknown-face dedup:** Same person across jittered frames produces
   exactly 1 event. Distinct people produce separate events.
2. **Snapshot decoupling:** When pipeline is running, snapshot reads from
   Redis and never opens RTSP connections.
3. **Corridor passersby:** Exterior sightings with no session create review
   events, not inside states or attendance records.
4. **Camera independence:** Stopping one camera doesn't affect the other.
   Snapshot for one camera doesn't affect the other.
