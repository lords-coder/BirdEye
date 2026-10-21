#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Celery worker for the camera pipeline + control/jobs queues.
#
# Used by docker-compose.yml (service `worker`) and runnable on any host:
#     bash scripts/run_worker.sh
#
# One child process per CAMERA_SLOTS queue, plus 2 children for the
# control/jobs queues, so a long-running camera task can never starve short
# control tasks (see app/workers/celery_app.py).  Combined with
# worker_prefetch_multiplier=1 this keeps a running camera from hoarding
# queued messages.
#
# Environment:
#   CAMERA_SLOTS     number of dedicated camera queues   (default 8)
#   CELERY_LOGLEVEL  celery log level                    (default info)
# ---------------------------------------------------------------------------
set -euo pipefail

: "${CAMERA_SLOTS:=8}"
export CAMERA_SLOTS

QUEUES="$(python - <<'PY'
from app.workers.celery_app import all_queues

print(",".join(all_queues()))
PY
)"

echo "[run_worker] slots=${CAMERA_SLOTS}"
echo "[run_worker] queues=${QUEUES}"

# --beat: the worker also runs the scheduler, because the beat_schedule in
# app/workers/celery_app.py (reap-dead-cameras every 60 s,
# check-period-deviations every 5 min, close-stale-sessions daily) is NOT
# executed by any other service in docker-compose.yml - without this, a
# crashed pipeline leaves phantom "running" state and stale slots forever.
# Set CELERY_NO_BEAT=1 to run a dedicated beat service instead.
if [ "${CELERY_NO_BEAT:-0}" = "1" ]; then
    BEAT_ARG=""
else
    BEAT_ARG="--beat"
fi

exec celery -A app.workers.celery_app:celery_app worker \
    --loglevel="${CELERY_LOGLEVEL:-info}" \
    --hostname="worker@%h" \
    ${BEAT_ARG} \
    --concurrency="$(( CAMERA_SLOTS + 2 ))" \
    --prefetch-multiplier=1 \
    --queues="${QUEUES}"
