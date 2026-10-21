# 00 — Executive Summary

**Project:** `ai-video-attendance` v1.0.0 — "AI Video Attendance System"
**Audit date:** 2026-10-14
**Audit scope:** Read-only inspection, static analysis, and safe verification of the entire repository at `C:\test\projects\ai-video-attendance-submission`.
**Constraint honored:** No application source file, configuration file, database, or dependency was modified. The only filesystem change made by this audit is the creation of this `PROJECT_AUDIT_REPORT/` directory.

---

## 1. Project purpose and current state

The project is a **Python/FastAPI backend for camera-based attendance**: it detects faces in RTSP/video-file streams (InsightFace `buffalo_l` = SCRFD detection + ArcFace 512-d embeddings), matches them against an enrolled student gallery (cosine similarity with threshold/margin/duplicate-identity rules), records attendance sessions per timetable period, and exposes a REST + WebSocket API plus a bundled vanilla-JS single-page admin console.

The codebase is **substantially implemented and internally coherent** — this is not a scaffold. 46 registered HTTP/WS routes, 8 SQLAlchemy tables with 4 consistent Alembic migrations, a real GPU inference service with cross-camera batching, a Celery worker topology, JWT multi-user auth with an approval workflow, encryption-at-rest for face embeddings, and 148 test functions across 13 test modules.

However, the project is **not deliverable as shipped**. The single most important conclusion of this audit:

> **The submission is missing critical deployment and onboarding artifacts that every document references — there is no `Dockerfile`, no `scripts/run_worker.sh`, no `.env.example`, no `scripts/setup_dev.*`, no `Makefile`, no `requirements-dev.txt`, and the entire `eval/` directory referenced by the README and CONTRIBUTING does not exist.** `docker compose up` fails at build; the documented 3-command quick start cannot be executed; and the worker container cannot start even if a Dockerfile existed.

Behind that packaging failure sit several **confirmed functional defects**, the most severe being that `POST /students/{id}/photos` (add reference photos to an enrolled student) can *never* succeed because of a type-confusion bug, and the demo/judge pipeline contains a guaranteed `TypeError`.

## 2. Overall implementation assessment

| Layer | Assessment |
|---|---|
| Core domain logic (matching, attendance state machine, crypto, quality gates) | **Good** — well-documented thresholds with measured derivations, defensively written, heavily unit-tested |
| API layer | **Good** — thin routes, consistent schemas, real validation; minor coupling smells |
| Auth (multi-user JWT, approval workflow, PBKDF2) | **Good** — the best-engineered part of the system |
| Inference/pipeline (GPU service, Celery, RTSP reader) | **Plausible but unverified** — code-complete, unit-tested in isolation, never validated end-to-end; several latent defects |
| Frontend SPA | **Functional but partial** — covers ~60 % of the backend surface; 3 runtime defects + 1 stored-XSS hole |
| Tests | **Broad (148 functions) but not runnable in this environment** (no Python interpreter present) |
| Deployment/packaging | **Broken** — missing Dockerfile, worker start script, env template |
| Documentation | **Aspirational and stale** — describes files, endpoints, and test counts that do not match the repository |
| Repository hygiene | **Poor** — 12 root-level ad-hoc dev scripts, personal file paths, and a real person's name + registration number in comments |

## 3. Major strengths

1. **Honest engineering culture in the code**: thresholds (0.40/0.10/0.90) carry measured derivations; README admits accuracy is unmeasured on real CCTV; encryption key has a production hard-fail with no JWT fallback.
2. **Real security primitives**: PBKDF2-SHA256 with per-password salts and constant-time compare (`app/users.py`), Fernet encryption of embeddings at rest, super-admin-only credential management, last-super-admin protection, non-enumerating forgot-password endpoint.
3. **Test suite breadth**: 148 test functions with fakeredis/Celery stubbing so no external services are needed; regression tests guarding a previously broken encrypt→store→decrypt→match chain.
4. **Clean layering**: `api → services → models`, inference behind an RPC abstraction, single model owner process.
5. **Frontend↔backend contract is 96 % clean**: all 35 HTTP calls + the WS URL map to real routes with correct methods and payload field names (verified call-by-call).

## 4. Major weaknesses

1. **Missing packaging artifacts** (Dockerfile, `run_worker.sh`, `.env.example`, `setup_dev.*`, `Makefile`, `requirements-dev.txt`, entire `eval/`): the product cannot be built, deployed, or onboarded as documented.
2. **Confirmed functional bugs** in enrollment (`add_photos` type confusion — endpoint always fails), demo tooling (`judge_demo.py` calls `main(argv)` on a no-arg `main()`), and the frontend (infinite WS reconnect loop after logout, background polling after logout, `total` field never returned).
3. **Security defects**: stored-XSS sink in the cameras table, unauthenticated `/media` mount serving enrolled reference photos, PII (real name + registration number) in two tracked root scripts — a direct violation of the project's own privacy rules.
4. **Documentation drift**: README claims 69 tests (actual: 148), a `/health` route and `/ws` endpoint that don't exist under those names, a provided Dockerfile that isn't, and quick-start scripts that aren't.
5. **Repository hygiene**: 12 ad-hoc root-level scripts (6 of which are in-place patchers of another script, one syntactically invalid), hardcoded personal `C:\Users\pc\...` paths in 4 scripts, and a leftover `print("DEBUG: ...")` in 7 backend locations.
6. **Unverifiable critical path**: no Python interpreter exists in the audit environment, so the 148-test suite, imports, and migrations could not be executed — every runtime claim rests on static reading.

## 5. Most important confirmed defects (top 5)

| # | Defect | Evidence | Impact |
|---|---|---|---|
| 1 | `enrollment.add_photos` treats the **encrypted base64 string** as a list of embeddings | `app/services/enrollment.py:495` (`list(student.embeddings or [])` iterates a `str`; only decrypted at L505) | `POST /students/{id}/photos` always returns 4xx for any enrolled student — the "add reference photos" feature is dead |
| 2 | **No `Dockerfile`** while `docker-compose.yml:18` requires `build: context: .`; worker command `bash scripts/run_worker.sh` (`docker-compose.yml:106`) references a **nonexistent file** | file listing; `docker-compose.yml` | `docker compose up` cannot build or start the worker — the documented full-stack deployment is impossible |
| 3 | `judge_demo.py:244` calls `setup_student_database.main(argv)` but `main()` accepts no arguments | `scripts/judge_demo.py:244` vs `scripts/setup_student_database.py:349` | Guaranteed `TypeError` in the judge demo's gallery-setup step (masked in tests by monkeypatching) |
| 4 | Migration/model type mismatch: `students.embeddings` is `sa.JSON()` in `0001_initial.py:29-34` but `String` with `server_default=""` in `app/models.py:42-44` | both files | A database created by `alembic upgrade head` has a JSON column the ORM treats as String — fresh production installs are at risk (tests bypass migrations via `create_all`) |
| 5 | Frontend WS reconnect guard is always true → infinite 4-second reconnect loop after logout; `logout()` also leaks the 5-second live-poll timer | `app/static/app.js:57` (`getToken()` returns `""`, not `null`), `app.js:578`, `app.js:60-67` | Post-logout background requests; login page hammered with doomed WS attempts; user yanked back to login card while typing on other auth cards |

## 6. Critical blockers

1. **Deployment blocker**: missing `Dockerfile` + `scripts/run_worker.sh` (+ no documented `celery -A` alternative).
2. **Onboarding blocker**: missing `scripts/setup_dev.ps1/.sh`, `.env.example`, `Makefile`, `requirements-dev.txt` — README §3/§11 and CONTRIBUTING §Setup are not executable.
3. **Verification blocker (environmental)**: no Python interpreter exists on this machine (`python` resolves to the Windows Store stub; no `py`, no venv, no conda). The test suite, type checks, lint, and migrations **could not be run**. This audit is therefore static-analysis-based; the README's historical claim "69 passed" is *unverified*, not disproved.
4. **Privacy blocker for redistribution**: `test_drive.py:10` and `test_drive2.py:10` contain a real person's name and registration number — the archive fails the project's own mandatory PII scan (CONTRIBUTING §"Verifying your archive": "must print 0").

## 7. Recommended next steps

1. **Repair phase P0** (see `09_RECOMMENDED_FIX_ROADMAP.md`): remove PII from root scripts, add the stored-XSS fix, gate `/media` behind auth, remove the 12 root debris scripts.
2. **P1**: fix `add_photos`, fix `judge_demo`, reconcile the `embeddings` column type (new migration), create `Dockerfile` + `run_worker.sh`, add `.env.example` + setup scripts or rewrite the README quick start to match reality.
3. **P2**: fix the three frontend runtime defects, the server-side `max_batch_frames` request/frame confusion, the dead Celery route trap, and the threshold/config inconsistencies.
4. **P3**: de-duplicate the ~90 %-identical summary functions, remove dead code, add pagination UI, add periods/alerts UI, refresh README.
5. **Restore verifiability**: provide a Python 3.11 environment (or container) and run the full suite before any repair claim is accepted; the suite is the safety net for everything above.

---

*Detailed findings with evidence: `05_BUGS_AND_DEFECTS.md` (defects), `06_SECURITY_AUDIT.md` (security), `08_MISSING_FEATURES_AND_COMPLETENESS.md` (coverage), `07_TEST_AND_BUILD_RESULTS.md` (verification attempts).*
