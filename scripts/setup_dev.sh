#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/setup_dev.sh - one-command dev bootstrap (idempotent), Linux/macOS.
#
# Does all seven steps documented in README section 3:
#   1. Python >= 3.11 check          5. alembic upgrade head
#   2. create .venv                  6. download model weights (non-fatal)
#   3. pinned install (both reqs)    7. run the test suite
#   4. .env from .env.example + generated EMBEDDING_ENCRYPTION_KEY
#
# Usage:  bash scripts/setup_dev.sh
#         SKIP_MODELS=1 bash scripts/setup_dev.sh   # skip step 6 (offline)
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\033[36m==> [%s/7] %s\033[0m\n' "$1" "$2"; }

step 1 "checking Python >= 3.11"
PYBIN="${PYTHON:-python3}"
command -v "$PYBIN" >/dev/null 2>&1 || { echo "python3 not found on PATH" >&2; exit 1; }
"$PYBIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python {sys.version_info[0]}.{sys.version_info[1]} found; >= 3.11 required")
print(f"    Python {sys.version_info[0]}.{sys.version_info[1]}")
PY

step 2 "creating .venv (idempotent)"
if [ ! -x .venv/bin/python ]; then "$PYBIN" -m venv .venv; fi
VPY=.venv/bin/python

step 3 "installing pinned dependencies (requirements.txt + requirements-dev.txt)"
"$VPY" -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt

step 4 ".env from .env.example + EMBEDDING_ENCRYPTION_KEY"
if [ ! -f .env ]; then
    cp .env.example .env
    KEY_LINE="$("$VPY" scripts/generate_key.py 2>/dev/null | grep '^EMBEDDING_ENCRYPTION_KEY=' | tail -n 1 || true)"
    if [ -n "$KEY_LINE" ]; then
        grep -v '^EMBEDDING_ENCRYPTION_KEY=' .env > .env.tmp && mv .env.tmp .env
        printf '%s\n' "$KEY_LINE" >> .env
        echo "    .env created; encryption key generated"
    else
        echo "    .env created; key generation failed - run: $VPY scripts/generate_key.py" >&2
    fi
else
    echo "    .env already exists - left untouched"
fi

step 5 "database migrations (alembic upgrade head)"
mkdir -p data
"$VPY" -m alembic upgrade head

step 6 "downloading model weights (buffalo_l, non-fatal)"
if [ -n "${SKIP_MODELS:-}" ]; then
    echo "    SKIP_MODELS set - skipped"
elif ! "$VPY" scripts/download_models.py; then
    echo "    model download failed/finished early - re-run: $VPY scripts/download_models.py" >&2
fi

step 7 "running the test suite"
set +e
"$VPY" -m pytest
TEST_EXIT=$?
set -e

echo
if [ "$TEST_EXIT" -eq 0 ]; then
    printf '\033[32msetup complete - start the API with:\033[0m\n  %s -m uvicorn app.main:app --reload\n' "$VPY"
else
    printf '\033[33msetup finished but the test suite reported failures (exit %s)\033[0m\n' "$TEST_EXIT"
fi
exit "$TEST_EXIT"
