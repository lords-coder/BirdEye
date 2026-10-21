# ---------------------------------------------------------------------------
# Celery beat scheduler (Windows counterpart to the `--beat` flag that
# scripts/run_worker.sh passes on Linux/Docker).
#
# Celery refuses --beat on Windows:
#     "-B option does not work on Windows. Please run celery beat as a
#      separate service."
# and docker-compose.yml defines no beat service either, so without this
# process the beat_schedule in app/workers/celery_app.py never fires:
#   * jobs.reap_dead_cameras        every 60 s   (phantom running state,
#                                                 stale slot owners)
#   * jobs.check_period_deviations  every 5 min  (absent/late/early alerts)
#   * jobs.close_stale_sessions     daily        (sessions open > 1 day)
#
# Usage (from the repo root, with the venv active):
#     powershell -File scripts/run_beat.ps1
#
# Environment:
#   CELERY_LOGLEVEL  beat log level   (default info)
#   BEAT_SCHEDULE_PATH  schedule db file (default %TEMP%\opencode\celerybeat-schedule)
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$env:PYTHONPATH = (Get-Location).Path

$python = Join-Path (Get-Location).Path ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$loglevel = if ($env:CELERY_LOGLEVEL) { $env:CELERY_LOGLEVEL } else { "info" }
$schedule = if ($env:BEAT_SCHEDULE_PATH) {
    $env:BEAT_SCHEDULE_PATH
} else {
    Join-Path $env:TEMP "celerybeat-schedule"
}

Write-Host "[run_beat.ps1] schedule=$schedule loglevel=$loglevel"

& $python -m celery -A app.workers.celery_app:celery_app beat `
    --loglevel=$loglevel `
    --schedule="$schedule" `
    --pidfile="$schedule.pid"
