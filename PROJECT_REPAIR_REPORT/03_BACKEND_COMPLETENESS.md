# 03 — Backend Completeness

The audit's "missing/broken functionality" findings, one by one: fixed,
completed, or honestly recorded as remaining. No new product features were
invented; "completed" means making the *documented, intended* behaviour work.

## 1. Completed in this phase

| Gap (audit) | State now | Evidence |
|---|---|---|
| `POST /students/{id}/photos` dead (BUG-01) | Works: decrypt-first append, correct index math, top-up beyond the initial 3–5 possible (BUG-31) | RR tests + 178-pass suite |
| `GET /attendance/logs` missing `total` (BUG-07) | Returns `items` + `total` (server-side count) | RR test + live smoke (`total` key present) |
| `INFERENCE_MODE=inprocess` unwired (BUG-13) | Worker installs the in-process engine inside `camera_run` when configured | RR test |
| Celery black-hole route (BUG-12) | Removed; worker consumes `control`, `jobs`, `camera_slot_0..N` only | `all_queues()` verified: `control,jobs,camera_slot_0,camera_slot_1` |
| No worker start script / no Dockerfile (BUG-02) | `scripts/run_worker.sh` + `Dockerfile` added; compose env (`DATA_DIR`, `INSIGHTFACE_ROOT`) wired to mounts | file review; compose stack itself still unverified (GPU) |
| Batch cap wrong unit (BUG-11) | Frame-based cap in the shared inference server | RR test |
| Alerts hard-coded policy (BUG-16) | 6 policy settings in `config.py`, documented in README §8 + `.env.example` | RR test; README table |
| Alerts day-boundary bug (BUG-18) | Local-day UTC window helper; grace windows configurable | RR test |
| Camera pipeline resilience (BUG-15/21/22) | Per-frame error isolation + rollback; confirm-buffer sweep/release; reply-length anomaly logged | RR tests |
| WS live feed revocation gap (BUG-24) | Full principal resolution incl. suspended/deleted users → `4401` | RR test |
| `/media` unauthenticated (SEC-03) | Authenticated + path-confined route | RR + live checks |
| Schema contract (BUG-23/SEC-02) | Camera create/update/out split; RTSP scheme validation server-side and rendered safely client-side | RR + live 422 |
| Model↔migration mismatch (BUG-04) | Migration `0005`; `alembic check` = no drift | `04_DATABASE_AND_MIGRATIONS.md` |
| Dev/bootstrap story (H-02 family) | `.env.example`, `.gitignore`, `requirements-dev.txt`, `Makefile`, `setup_dev.ps1`/`.sh` — `setup_dev.ps1` verified end-to-end (venv → deps → key → migrate → 178-test suite, exit 0) | `01_BASELINE_AND_FINAL_TESTS.md` |
| Quality-gate outage semantics (BUG-33, new) | 503 with the real cause on all three paths (single enroll / bulk / API) | 3 RR tests + live smoke |
| Duplicate-identity detection correctness (BUG-17/32, new) | Single-threshold config; single-row gallery no longer NaN-blind | RR tests |

## 2. Known completeness gaps that REMAIN (not forgotten)

| Gap | Status | Where planned |
|---|---|---|
| `eval/` toolkit (roadmap, calibration, diagnostics — 7 files referenced by older docs) | **Absent from the archive and deliberately not fabricated.** README/ARCHITECTURE now point to `scripts/check_accuracy.py` and state the gap | `06_REMAINING_ISSUES.md` |
| Review queue, attendance disputes, RBAC granularity, data retention job | Roadmap features (never built); alerts/acknowledge exist | audit `09_RECOMMENDED_FIX_ROADMAP.md` |
| Rate limiting / lockout / password-reset abuse controls | Security hardening, deferred by scope | `07_NEXT_PHASE_SECURITY_PLAN.md` |
| Quality gate on `POST /{id}/photos` and `PUT /{id}/face` | **Observation:** gates run on create + bulk-import but not on add/replace. Pre-existing design; changing it alters behaviour — needs a decision, see 06 | `06_REMAINING_ISSUES.md` |
| `engine.py` live path applies only area/brightness/sharpness gates, not the config's pose/width/det-score gates | Pre-existing (GPU-unverified module), documented in audit; untouched | `06_REMAINING_ISSUES.md` |
| Response models for some list endpoints are bare `dict` | Cosmetic contract gap; OpenAPI still documents shapes | audit finding, low priority |

## 3. Contract summary after repair

- Every route that can fail for *infrastructure* reasons (DB, Redis, inference)
  now either degrades gracefully with a logged warning or returns **503 with
  the real cause** — no more outage-masquerading-as-validation (BUG-33 was the
  last instance found).
- All policy thresholds the pipeline uses are single-sourced in
  `app/config.py` (BUG-16/17/18 closed the exceptions).
- The database schema matches the models exactly (`alembic check` clean), and
  one migration path exists from every shipped revision to head.
