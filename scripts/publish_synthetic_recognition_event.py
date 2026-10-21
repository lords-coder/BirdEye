"""Publish ONE synthetic recognition event through the real pipeline path.

Verification harness for PROJECT_RELEASE_REPORT/18_REALTIME_RECOGNITION_LOGS.md.

Physical cameras are not available in this deployment (both camera rows are
metadata-only: stream_path is NULL), so the recognition pipeline cannot
produce a genuine sighting.  This script reproduces exactly what
``app.pipeline.camera_task._handle_faces`` does after a dedup-passing
unknown-face detection:

    1. commit a ``RecognitionLog`` row (student_id NULL = unverified face),
    2. publish ``recognition_log`` for that row through
       ``app.redis_client.publish_event`` on the real Redis bus.

No attendance state, no student record, no camera connection and no alert is
created - it is the smallest faithful reproduction of the publish path, used
to prove the WebSocket -> Recognition Logs delivery while the page is open
and no camera preview is running.

Run inside the API container:

    Get-Content scripts/publish_synthetic_recognition_event.py -Raw |
        docker compose exec -T api python -

Output: a single JSON line with the created row id and timestamp.
"""
from __future__ import annotations

import json

from app.db import SessionLocal
from app.models import Camera, RecognitionLog
from app.redis_client import get_redis, publish_event
from app.services.events import recognition_log_event
from app.timeutil import utcnow

CONFIDENCE = 0.4242  # deliberately distinctive: easy to spot in the console
INFERENCE_MS = 7.5


class _StandInCamera:
    """Camera stand-in for a deployment with no camera rows.

    Mirrors the fields ``recognition_log_event`` reads; id stays None so the
    published payload matches the row's NULL camera_id.
    """

    id = None
    name = "synthetic-verification"
    type = "entry"
    location = None


def main() -> None:
    db = SessionLocal()
    try:
        camera = db.query(Camera).order_by(Camera.id).first()
        ts = utcnow()
        if camera is None:
            # Clean deployment DB (no camera rows).  The audit table allows a
            # NULL camera_id (kept when a camera is deleted), so write that
            # and publish with a stand-in camera carrying the same null id -
            # the console then renders "—" exactly as it would for a REST row.
            camera = _StandInCamera()

        row = RecognitionLog(
            student_id=None,  # unverified face - no student data involved
            camera_id=camera.id,
            timestamp=ts,
            confidence_score=CONFIDENCE,
            gpu_inference_time_ms=INFERENCE_MS,
        )
        db.add(row)
        db.flush()  # assign the audit-row id before publishing it
        log_id = int(row.id)
        db.commit()

        publish_event(
            get_redis(),
            recognition_log_event(
                log_id=log_id,
                camera=camera,
                student=None,
                confidence=CONFIDENCE,
                inference_ms=INFERENCE_MS,
                timestamp=ts,
                kind="unknown",
            ),
        )
        print(json.dumps({"log_id": log_id, "camera_id": camera.id,
                          "ts": ts.isoformat()}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
