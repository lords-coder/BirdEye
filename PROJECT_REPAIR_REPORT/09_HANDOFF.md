# 09 — Handoff

## 1. What this archive is now

A working, tested backend for camera-based attendance (SRM University
Haryana), repaired to the state described in this report. The audit
(`PROJECT_AUDIT_REPORT/`) is the defect history; this report is the repair
record. Start reading at `00_REPAIR_SUMMARY.md`.

## 2. One-command bring-up (verified on Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
# idempotent: python>=3.11 check -> venv -> pinned deps -> .env + generated
# encryption key -> alembic upgrade head -> model weights -> full test suite
```

Then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload      # API + web UI
.\.venv\Scripts\python.exe -m pytest                              # 178 passed, 1 skipped
.\.venv\Scripts\python.exe scripts\smoke_test.py http://localhost:8000
```

Linux/macOS: `bash scripts/setup_dev.sh`, `scripts/run_worker.sh` for the
Celery worker, `make` for the common targets.

Full stack (GPU host): `docker compose up -d` — **still unverified end-to-end**
(see `06_REMAINING_ISSUES.md` §1); run `scripts/smoke_test.py` after boot and
expect enrollment to go 201 rather than the degraded 503 seen without the
inference service.

## 3. Day-1 verification checklist (what to trust)

| Claim | How to re-verify |
|---|---|
| 178 passed / 1 skipped | `.\.venv\Scripts\python.exe -m pytest` |
| No model↔migration drift | `.\.venv\Scripts\python.exe -m alembic check` |
| API boots + auth behaves | `scripts\smoke_test.py http://localhost:8000` (0 failed expected) |
| No PII / personal paths | search the archive for `C:\Users\`, personal names; expect 0 |
| No media in archive | file search for mp4/jpg/png; expect 0 (only `samples/README.md`) |
| Quality gate fails honestly | with inference down, `POST /students` → 503 with the real cause |

## 4. Highest-value next work (ordered)

1. **GPU host validation** — compose stack + real RTSP camera (`06` §1). This
   unblocks the single most important open question: real-camera accuracy.
2. **P0 security items** — dependency audit, rate limiting, reset-abuse
   controls (`07` §2).
3. **Decision items** — confirmation rule 3/10 vs 2/5, quality-gate coverage
   on add/replace, institutional grace-window values (`06` §4).
4. **`eval/` toolkit rebuild** — audit roadmap item #1 for calibration.

## 5. Conventions to keep (from CONTRIBUTING, now accurate)

- One recognition threshold, sourced from `app/config.py` only.
- Every bug fix ships with a regression test
  (`tests/test_repair_regressions.py` is the pattern to copy).
- No real names or registration numbers anywhere; scans must print 0.
- Do not pass a second `-q` to pytest (`addopts` already sets it).
- Record raw command output for any claimed number.

## 6. Environment facts of this repair machine (so nobody chases ghosts)

- Windows, Python 3.11.9 in `.venv`; **no Redis, no Docker, no GPU, no camera**
  — hence the two "degraded" smoke rows and the GPU-stack caveat.
- `.env` + `data/attendance.db` exist locally (gitignored); safe to delete,
  recreated by `setup_dev`.
- Test artifacts: `%TEMP%\opencode\pytest_T0.txt` (baseline),
  `pytest_T1.txt`, `pytest_FINAL.txt`.

## 7. File map of this report

`00` summary · `01` tests · `02` fixed defects (with proofs) · `03`
completeness · `04` DB/migrations · `05` integration/API · `06` remaining
issues · `07` security plan · `08` change manifest (every touched file) ·
`09` this file.
