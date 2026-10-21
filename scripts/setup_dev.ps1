# ---------------------------------------------------------------------------
# scripts/setup_dev.ps1 - one-command dev bootstrap (idempotent), Windows.
#
# Does all seven steps documented in README section 3:
#   1. Python >= 3.11 check          5. alembic upgrade head
#   2. create .venv                  6. download model weights (non-fatal)
#   3. pinned install (both reqs)    7. run the test suite
#   4. .env from .env.example + generated EMBEDDING_ENCRYPTION_KEY
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
#
# Set SKIP_MODELS=1 to skip step 6 (offline machines); step 7 still runs.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step([int]$n, [string]$msg) {
    Write-Host "==> [$n/7] $msg" -ForegroundColor Cyan
}

Step 1 "checking Python >= 3.11"
# Probe candidates and validate actual output: on Windows the Microsoft
# Store "python.exe" app-execution alias exists as a stub that prints an
# install hint instead of running - a Get-Command check alone is not enough.
$candidates = @()
if ($env:PYTHON) { $candidates += $env:PYTHON }
foreach ($cmd in @('python', 'python3', 'py')) {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($c) { $candidates += $c.Source }
}
$PYBIN = $null
$verLine = $null
foreach ($cand in $candidates) {
    $out = & $cand -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -Last 1
    if ($out -match '^\d+\.\d+$') {
        $verLine = $out
        $PYBIN = $cand
        break
    }
}
if (-not $PYBIN) {
    Write-Host "no working Python found on PATH (install Python 3.11+ and/or set `$env:PYTHON)" -ForegroundColor Red
    exit 1
}
$major, $minor = $verLine.Split('.') | ForEach-Object { [int]$_ }
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
    Write-Host "Python $verLine found; >= 3.11 required" -ForegroundColor Red
    exit 1
}
Write-Host "    Python $verLine ($PYBIN)"

Step 2 "creating .venv (idempotent)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $PYBIN -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$VPy = ".venv\Scripts\python.exe"

Step 3 "installing pinned dependencies (requirements.txt + requirements-dev.txt)"
& $VPy -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Write-Host "dependency install failed" -ForegroundColor Red; exit $LASTEXITCODE }

Step 4 ".env from .env.example + EMBEDDING_ENCRYPTION_KEY"
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    $keyOut = & $VPy scripts\generate_key.py 2>$null
    $keyLine = $keyOut | Where-Object { $_ -match '^EMBEDDING_ENCRYPTION_KEY=' } | Select-Object -Last 1
    if ($keyLine) {
        # drop the empty placeholder first so exactly one value remains
        (Get-Content .env) | Where-Object { $_ -notmatch '^EMBEDDING_ENCRYPTION_KEY=' } | Set-Content .env
        Add-Content .env $keyLine
        Write-Host "    .env created; encryption key generated"
    } else {
        Write-Host "    .env created; key generation failed - run: $VPy scripts\generate_key.py" -ForegroundColor Yellow
    }
} else {
    Write-Host "    .env already exists - left untouched"
}

Step 5 "database migrations (alembic upgrade head)"
New-Item -ItemType Directory -Force -Path "data" | Out-Null
& $VPy -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { Write-Host "migrations failed" -ForegroundColor Red; exit $LASTEXITCODE }

Step 6 "downloading model weights (buffalo_l, non-fatal)"
if ($env:SKIP_MODELS) {
    Write-Host "    SKIP_MODELS set - skipped"
} else {
    & $VPy scripts\download_models.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    model download failed/unfinished - re-run: $VPy scripts\download_models.py" -ForegroundColor Yellow
    }
}

Step 7 "running the test suite"
& $VPy -m pytest
$testExit = $LASTEXITCODE

Write-Host ""
if ($testExit -eq 0) {
    Write-Host "setup complete - start the API with:" -ForegroundColor Green
    Write-Host "  $VPy -m uvicorn app.main:app --reload"
} else {
    Write-Host "setup finished but the test suite reported failures (exit $testExit)" -ForegroundColor Yellow
}
exit $testExit
