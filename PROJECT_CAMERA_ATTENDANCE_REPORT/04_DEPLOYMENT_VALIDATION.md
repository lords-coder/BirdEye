# Deployment Validation (04)

Validated 2026-10-15 on this machine (native stack; processes started and
stopped by the implementer — none left running):

| service | result |
|---|---|
| API (uvicorn :8765) | up; `/system/health` → db up, redis up, inference online |
| Redis (portable, :6379) | up for the run; stopped after |
| Celery worker (threads, slots 0–1) | ran pipeline tasks; stopped after |
| Inference (buffalo_l, CUDAExecutionProvider, RTX 5060) | model loaded, warmup 2714 ms; stopped after |
| Model weights | `~/.insightface/buffalo_l` extracted (~325 MB) — present, no download needed |
| WS event delivery | 9 live events incl. `presence_state` during E2E |
| E2E (real components, synthetic faces) | 18/18; validation data fully cleaned afterward |
| Persistent storage | SQLite dev DB; validation artifacts removed (dev DB holds only seed admin + 2 camera metadata rows) |
| Health/restart | health endpoint verified; no orphan processes (one foreign system-python worker observed and left untouched) |

## Blocked (reported, not substituted)

- **PostgreSQL runtime**: no server on this machine; migrations are
  Postgres-compatible and SQLite-verified (`alembic check` clean).
- **Docker runtime**: no Docker/WSL; `validate_compose.py` 14 pass / 1
  pre-existing warn is static only.
- **Production DB**: never touched; no resets performed (convention kept).

## Clean-deployment note

Weights resolve from the standard insightface cache; no
developer-machine-only config is required beyond documented `.env`
settings. First CUDA inference incurs the ~1 min one-time init (observed
again this run: engine ready ≈60 s after start).
