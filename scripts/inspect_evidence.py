"""Recent recognition / presence / session evidence (read-only)."""
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import AttendanceSession, PresenceState, RecognitionLog

db = SessionLocal()
cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

q = db.query(RecognitionLog).filter(RecognitionLog.timestamp >= cutoff)
print("=== recognition logs (last 6h) ===")
print("  count:", q.count())
for x in q.order_by(RecognitionLog.timestamp.desc()).limit(30):
    print(
        f"  id={x.id} cam={x.camera_id} student={x.student_id} "
        f"sim={x.confidence_score} gpu_ms={x.gpu_inference_time_ms} "
        f"ts={x.timestamp}"
    )

print("\n=== ALL recognition logs by student (today-ish) ===")
tot = db.query(RecognitionLog).count()
print("  total rows ever:", tot)
known = (
    db.query(RecognitionLog)
    .filter(RecognitionLog.student_id.isnot(None))
    .count()
)
print("  matched rows:", known, " unknown(NULL) rows:", tot - known)

print("\n=== presence states ===")
for p in db.query(PresenceState).all():
    print(f"  student={p.student_id} state={p.state} updated={p.updated_at}")

print("\n=== attendance sessions (latest 10) ===")
for s in (
    db.query(AttendanceSession)
    .order_by(AttendanceSession.id.desc())
    .limit(10)
    .all()
):
    print(
        f"  id={s.id} student={s.student_id} period={s.period_id} "
        f"in={s.entry_time} out={s.exit_time} date={s.date} "
        f"status={s.status} dur={s.total_duration} src={s.source}"
    )

db.close()
