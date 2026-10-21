# ---------------------------------------------------------------------------
# Windows counterpart to scripts/run_worker.sh.
#
# The bash script cannot run on a Windows host (no bash / no exec), so the
# queue list is derived from the SAME project function (all_queues()) rather
# than being retyped by hand - a hand-typed list previously produced
# camera_slot_1..8 while the API dispatches to camera_slot_0..7, leaving
# every camera.start request unconsumed.
#
# Usage (from the repo root, with the venv active):
#     powershell -File scripts/run_worker.ps1
#
# Environment:
#   CAMERA_SLOTS     number of dedicated camera queues   (default 8)
#   CELERY_LOGLEVEL  celery log level                    (default info)
#
# Pool selection
# --------------
# Linux uses the default prefork pool (one OS process per camera slot), which
# is what app/pipeline/camera_task.py was originally written against.
#
# Windows CANNOT use prefork: billiard's pool children die at startup with
#     PermissionError: [WinError 5] Access is denied
# on the named semaphores shared with the parent (reproduced on this host
# with Celery 5.6.3 / billiard).  The supported Windows configuration is the
# threads pool.  That is safe here because every per-camera resource is
# created INSIDE run_camera_task (its own SessionLocal(), its own
# EmbeddingIndex, its own FrameReader thread), and the GPU model is never
# loaded in the worker in INFERENCE_MODE=redis - all heavy work happens in
# the separate inference service.  The one shared module-level structure,
# camera_task._CONFIRM_BUFFER, is now guarded by _CONFIRM_LOCK.
#
# Override with CELERY_POOL if needed (e.g. `solo` for single-camera debug).
#
# Beat
# ----
# Windows Celery refuses `--beat` in-process ("-B option does not work on
# Windows"), and docker-compose/this host run NO other scheduler, so the
# beat_schedule (reap-dead-cameras, check-period-deviations,
# close-stale-sessions) needs a separate process.  Start it with:
#     powershell -File scripts/run_beat.ps1
# (scripts/run_worker.sh passes --beat on Linux/Docker instead.)
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $env:CAMERA_SLOTS) { $env:CAMERA_SLOTS = "8" }
$env:PYTHONPATH = (Get-Location).Path

$python = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# Derive the queue list from the project itself - never hand-write it.
# (A hand-typed list previously produced camera_slot_1..8 while the API
#  dispatches to camera_slot_0..7, so no camera job was ever consumed.)
$queues = & $python -c "from app.workers.celery_app import all_queues; print(','.join(all_queues()))"
if ($LASTEXITCODE -ne 0) { throw "could not derive celery queues" }

$concurrency = [int]$env:CAMERA_SLOTS + 2
$loglevel = if ($env:CELERY_LOGLEVEL) { $env:CELERY_LOGLEVEL } else { "info" }
$pool = if ($env:CELERY_POOL) { $env:CELERY_POOL } else { "threads" }

Write-Host "[run_worker.ps1] slots=$env:CAMERA_SLOTS concurrency=$concurrency pool=$pool"
Write-Host "[run_worker.ps1] queues=$queues"

& $python -m celery -A app.workers.celery_app:celery_app worker `
    --loglevel=$loglevel `
    --hostname="worker@%h" `
    --pool=$pool `
    --concurrency=$concurrency `
    --prefetch-multiplier=1 `
    --queues=$queues
