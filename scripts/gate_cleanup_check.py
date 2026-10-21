"""Verify no phantom camera runtime state was left in Redis by the gate."""
import redis

from app.config import settings

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
keys = sorted(r.keys("cam:*"))
print("remaining cam:* keys:", keys if keys else "NONE (clean)")
for c in (1, 2):
    print(f"  camera {c} state={r.get(f'cam:{c}:state')!r} "
          f"hb={r.get(f'cam:{c}:hb')!r} "
          f"frame={r.get(f'cam:{c}:frame') is not None}")
