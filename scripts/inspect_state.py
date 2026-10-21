"""Inspect current roster / camera / attendance state (read-only)."""
from app.db import SessionLocal
from app.models import (
    AttendanceSession,
    Camera,
    PresenceState,
    RecognitionLog,
    Student,
)

db = SessionLocal()
print("=== students ===")
for s in db.query(Student).all():
    has_emb = "yes" if s.embeddings else "NO"
    print(f"  id={s.id} name={s.name!r} reg={s.registration_no!r} "
          f"section={s.section!r} embeddings={has_emb} "
          f"photos={len(s.photo_paths or [])}")

print("=== cameras ===")
for c in db.query(Camera).all():
    print(f"  id={c.id} {c.name!r} role={c.camera_role} type={c.type} "
          f"enabled={c.enabled} sampling_rate={c.sampling_rate} "
          f"last_frame_at={c.last_frame_at} last_error={c.last_error!r}")

print("=== counts ===")
print("  sessions         :", db.query(AttendanceSession).count())
print("  recognition_logs :", db.query(RecognitionLog).count())
print("  presence_states  :", db.query(PresenceState).count())
db.close()
