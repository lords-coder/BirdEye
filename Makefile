# ---------------------------------------------------------------------------
# AI Video Attendance - common developer entry points (README section 3).
#
# Assumes the dev venv exists (run `make setup` first).  On Windows without
# a POSIX make, call the scripts directly - see README quick start.
#
# Variables:
#   PYTHON   interpreter to use        (default .venv/bin/python)
#   CSV      enrollment CSV for `make enroll`
#   VIDEO    clip for `make demo`
# ---------------------------------------------------------------------------
PYTHON ?= .venv/bin/python
CSV    ?=
VIDEO  ?=

.PHONY: setup test run-api demo enroll calibrate

## Create .venv, install pinned deps, .env + key, migrations, tests
setup:
	bash scripts/setup_dev.sh

## Run the full test suite
test:
	$(PYTHON) -m pytest

## Start the API with auto-reload
run-api:
	$(PYTHON) -m uvicorn app.main:app --reload

## End-to-end demo (pass VIDEO=/path/to/clip.mp4)
demo:
	$(PYTHON) scripts/run_demo.py $(if $(VIDEO),--video $(VIDEO),)

## Bulk enrollment from a CSV (pass CSV=/path/to/students.csv)
enroll:
	$(PYTHON) scripts/setup_student_database.py --csv $(CSV)

## Threshold calibration harness
calibrate:
	@echo "eval/ (eval/enroll_and_calibrate.py, eval/diag_threshold.py) is NOT" >&2
	@echo "included in this archive - see PROJECT_REPAIR_REPORT/06_REMAINING_ISSUES.md" >&2
	@exit 1
