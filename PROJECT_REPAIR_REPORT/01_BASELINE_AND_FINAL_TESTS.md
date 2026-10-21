# 01 — Baseline and Final Tests

## 1. Environment note (why a true T0 was impossible)

The audit phase ran **without any Python interpreter** on the machine, so no
test could execute before this phase. Repair step 1 rebuilt the environment:

- Python 3.11.9 (winget) → `C:\Users\tanss\AppData\Local\Programs\Python\Python311\`
- project venv → `.venv\` (in the project root, gitignored)
- `pip install -r requirements.txt` + `requirements-dev.txt`
  (pytest 9.1.1, fakeredis 2.40.0, httpx 0.28.1 — test-time only)

**T0 baseline was therefore captured immediately after the first low-risk edit
batch** (comments + settings *additions* in `app/config.py`; no behaviour
changes). This is the closest honest approximation of a pre-repair baseline.

## 2. Baseline (T0)

```
command:  .venv\Scripts\python.exe -m pytest
result:   151 passed, 1 skipped, 1 warning in 15.30s
exit:     0
artifact: %TEMP%\opencode\pytest_T0.txt
```

The single skip is `tests/test_pipeline_e2e.py` (needs
`tests/assets/test_multi.mp4`, deliberately not shipped — the archive contains
no media). `conftest.py` checks for the file and skips gracefully.

## 3. Checkpoint run (after backend fixes + new regression suite)

```
command:  .venv\Scripts\python.exe -m pytest
result:   175 passed, 1 skipped in 20.37s
exit:     0
artifact: %TEMP%\opencode\pytest_T1.txt
```

(+24 tests vs T0: the first version of `tests/test_repair_regressions.py`.)

## 4. Final run (finished tree)

```
command:  .venv\Scripts\python.exe -m pytest
result:   178 passed, 1 skipped in 12.48s
exit:     0
artifact: %TEMP%\opencode\pytest_FINAL.txt
```

**179 collected** (`pytest --collect-only -q`), of which the skip above.

| Group | Tests | Note |
|---|---|---|
| pre-existing suite | 150 pass + 1 skip | minus the old `test_registration.py`, which was rewritten |
| `tests/test_repair_regressions.py` (new) | 25 | one+ per fixed defect |
| `tests/test_registration.py` (rewritten, H-03) | 4 | pattern-driven, not hard-coded |

No pre-existing test was deleted or weakened. `tests/test_registration.py`
was rewritten because it hard-coded a registration pattern that the code no
longer uses; it now derives expectations from
`settings.registration_no_pattern` / `_validate_reg_no`.

## 5. Live server verification (beyond pytest)

Redis, GPU inference and a camera are absent on this machine, so verification
used the API alone in its honest degraded mode:

```
uvicorn app.main:app --host 127.0.0.1 --port 8765     # started, waited for readiness
scripts\smoke_test.py http://127.0.0.1:8765           # the repo's own smoke suite
result: 21 checks: 19 passed, 2 degraded, 0 failed
exit:   0
```

The 2 degraded checks are **correct behaviour**, and both print the real cause:

- `POST /students` (enroll) → `503 inference service unavailable: redis
  unavailable: …6379…` (inference service genuinely down here)
- `POST /cameras/{id}/start` → `503 needs redis+worker`

A first live run *before* the BUG-33 fix returned a misleading
`400 quality check failed: no face detected` for the enrollment probe; after
the fix it returns the honest `503`. Full detail in
`05_INTEGRATION_AND_API_REPORT.md`.

## 6. Static / structural checks (final tree)

```
python -m compileall app scripts tests alembic     → exit 0
alembic check                                      → "No new upgrade operations detected."
alembic heads                                      → 0005_embeddings_text (head)
personal-path scan (C:\Users\, OneDrive, AppData)  → 0 matches
root-level *.py files                              → 0
media files (*.mp4/*.jpg/*.png/…)                   → 0
```

## 7. How to reproduce

```powershell
# from the project root, Windows:
.\.venv\Scripts\python.exe -m pytest                 # full suite (no extra -q!)
.\.venv\Scripts\python.exe -m pytest tests\test_repair_regressions.py
.\.venv\Scripts\python.exe -m alembic check          # needs .env DATABASE_URL
```

> `pyproject.toml` already sets `addopts = "-q"`; passing another `-q` creates
> `-qq`, which hides the `N passed` summary line.
