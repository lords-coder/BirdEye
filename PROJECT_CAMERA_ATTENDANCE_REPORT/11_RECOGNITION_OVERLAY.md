# Recognition Overlay (11)

## Implementation

`app/pipeline/annotate.py` — pure CPU drawing on a copy of the pipeline's
own frame. No second recognition pipeline exists.

### Detection data source

The pipeline already runs SCRFD detection + ArcFace embedding via the
shared GPU inference service. `build_detections()` receives those exact
results (`face["bbox"]`, `face["embedding"]`) and matches each embedding
through the same `EmbeddingIndex` the attendance path uses. This runs on
the **last frame of each batch** only — one extra cosine match per frame,
no additional GPU call.

### Visual states

| Condition | Box colour | Label |
|---|---|---|
| `match.student_id` found, confidence ≥ threshold | Green | `Name \| RECOGNIZED` |
| Below threshold or ambiguous (margin rule) | Amber | `UNKNOWN / UNVERIFIED` |
| Recognised, presence state INSIDE, period active, below threshold | Green + sub-label | `IN CLASS · 18/30 MIN` (actual minutes / actual threshold) |
| Recognised, official record says PRESENT | Green + sub-label | `PRESENT` |
| Recognised, presence ENTRY_PENDING / EXIT_PENDING / NEEDS_REVIEW | Green + sub-label | `VERIFYING…` |
| No embedding returned by inference | Amber | `UNVERIFIED` |

A face match alone never prints `PRESENT`. That status is shown only when
a stored `AttendanceRecord` with `effective_status == PRESENT` exists for
the active period — the backend is the source of truth.

### Threshold arithmetic

`threshold_s` comes from `attendance_engine.threshold_seconds(period, window)`,
which applies the per-period `required_minutes`/`required_ratio` override
or the global default. The `18/30` sub-label is computed from the student's
actual tracked presence today and that threshold — not hardcoded.

### Exit events

Exit notifications appear in the event feed (and WS `presence_state` /
`recognition` events), not as a face-overlay state. A face disappearing
from frame never produces an exit event.