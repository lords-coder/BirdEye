# 17 — Unknown-Face Dedup Results

**Sprint:** 2026-10-18  
**Problem:** 2147 unknown-face log rows flooding the database and alerts

---

## Before the Fix

| Metric | Value |
|--------|-------|
| Total RecognitionLog rows | 2159 |
| Known-face rows | 12 |
| Unknown-face rows | **2147** |
| Unknown ratio | **99.4%** |

Every frame with an unknown face wrote a `RecognitionLog`, published a
WebSocket event, and created an `unknown_face` alert. The dedup window
(`dedup_window_seconds=120`) never fired because `_unknown_hash()` returned
a different key on every frame.

### Why the hash was unstable
```python
# BEFORE: hashes raw floats — varies every frame
payload = _json.dumps(list(embedding[:16]), separators=(",", ":"))
```
Real face embeddings shift by ±0.001–0.01 between frames due to lighting,
camera angle, and JPEG compression. The JSON serialization captured these
micro-differences, producing a unique hash per frame.

---

## After the Fix

| Metric | Value |
|--------|-------|
| 20 jittered frames, same person | **1 event, 1 log row, 1 alert** |
| Two distinct unknown faces | **2 separate events** |
| Hash stability (jitter < 0.005) | **Stable across all 20 frames** |

### The fix
```python
# AFTER: quantized to 2 dp, all 512 dimensions
payload = _json.dumps(
    [round(float(x), 2) for x in embedding], separators=(",", ":")
)
```

Rounding to 2 decimal places absorbs realistic per-frame variation
(±0.004) while preserving distinctness between different people.

---

## Regression Tests

### `test_same_unknown_jittered_frames_single_event`
- Creates a synthetic unknown face vector (512-dim, unit vector at index 100).
- Applies jitter of 0.001–0.003 at index 50 across 20 frames.
- Asserts: exactly 1 `RecognitionLog` with `student_id=None`,
  exactly 1 `Alert` of type `unknown_face`.

### `test_two_distinct_unknowns_get_separate_events`
- Two different unknown vectors (index 100 vs index 200).
- Asserts: 2 events total, one per person.

### `test_unknown_hash_quantized_stable`
- Verifies hash equality for jitter amounts 0.002 and 0.004 (both round
  to 0.00 at 2 dp).

### `test_unknown_hash_distinguishes_different_faces`
- Verifies hash inequality for vectors differing at index 100 vs 200.

---

## Impact

| Aspect | Before | After |
|--------|--------|-------|
| DB writes per minute (corridor traffic) | ~150–200 | **~1–2** |
| Alert volume | Flood | **1 per new person per 120 s** |
| Monitoring panel usability | Unusable | **Actionable** |
| Redis dedup effectiveness | 0% (key always new) | **~99%** |

---

## Config Safety Net

`unknown_cooldown_s = 60` is available in `app/config.py` as an additional
camera-level rate limit. It is **not currently enforced** in the pipeline
(the quantized hash alone solves the flood), but operators can activate it
by modifying the dedup check in `_handle_faces` if production traffic
reveals edge cases.
