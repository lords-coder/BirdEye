# 07 — Test and Build Results

## 0. Environment assessment (the governing constraint)

| Probe | Command | Result |
|---|---|---|
| Python on PATH | `python --version` | **FAIL** — resolves to the Windows Store alias stub ("Python was not found" + store redirect) |
| Python launcher | `py --version` | **FAIL** — not installed |
| pip | `pip --version` | **FAIL** — not recognized |
| git | `git --version` | **FAIL** — not installed (archive has no `.git` either) |
| Known install locations | `C:\Python*`, `%LOCALAPPDATA%\Programs\Python*`, `py` launcher, anaconda/miniconda paths, project `.venv`/`venv` | **NONE FOUND** |

**Consequence:** no test run, no import check, no compile check, no migration run, no lint, no type check, no dependency audit, no server boot was possible. Every runtime claim in this report rests on static code analysis. This is recorded as an **environmental BLOCKER**, not as a project failure — but it also means the README's historical claim "`69 passed` on 2026-10-04" is **UNVERIFIED** (and its count is stale regardless: 148 test functions exist).

To restore verifiability, the repair phase must first provide a Python 3.11+ interpreter with `requirements.txt` + a dev requirements set installed.

## 1. Commands executed (complete list)

| # | Command / check | Type | Result |
|---|---|---|---|
| 1 | `Get-ChildItem -Recurse -Force -File` (excluding caches) | inventory | 104 meaningful files; no hidden config files found |
| 2 | `Test-Path` for `Dockerfile`, `Makefile`, `.env.example`, `.gitignore`, `requirements-dev.txt`, `scripts/setup_dev.ps1/.sh`, `scripts/run_worker.sh`, `eval/`, `data/` | existence check | **ALL FALSE** (missing artifacts confirmed) |
| 3 | `python --version` / `py --version` / `pip --version` / `git --version` | runtime probe | all unavailable (see §0) |
| 4 | Regex count `^\s*(async\s+)?def test_` over `tests/*.py` | static count | **148 test functions** (13 modules + 1 non-test fixture function in conftest) vs README's claimed 69 |
| 5 | Grep `@router\.(get\|post\|put\|delete\|patch\|websocket)\(` over `app/api/` | static count | 46 route handlers (45 HTTP + 1 WS) + 2 in `main.py` = 48 |
| 6 | Grep `TODO\|FIXME\|XXX\|HACK` over `*.py` | hygiene scan | **0 matches** — no formal TODO markers anywhere |
| 7 | Grep for PII pattern (real name/registration from script comments) | privacy scan | **2 matches** (`test_drive.py:10`, `test_drive2.py:10`) — see SEC-01 |
| 8 | Grep `setup_student_database\|main\(` in `judge_demo.py` + definition in `setup_student_database.py` | defect verification | BUG-03 confirmed (`main(argv)` call vs no-arg `main()`) |
| 9 | Full read of `alembic/versions/0001_initial.py` vs `app/models.py` | schema consistency | BUG-04 confirmed (JSON vs String on `students.embeddings`) |
| 10 | Frontend↔backend call-site cross-check (36 call sites vs 48 routes + schemas) | contract check | all calls resolve; 7 drift items (see 03 §3) |

## 2. Checks that could NOT be performed (and why)

| Check | Planned method | Status | Blocker |
|---|---|---|---|
| Unit/integration test suite (148 functions) | `python -m pytest` | **BLOCKED** | no interpreter |
| Byte-compile / import check of all 46 app modules | `python -m compileall app` | **BLOCKED** | no interpreter |
| FastAPI app boot + OpenAPI schema generation | `python -c "from app.main import app; print(len(app.routes))"` | **BLOCKED** | no interpreter |
| Alembic upgrade on scratch DB | `alembic upgrade head` (SQLite) | **BLOCKED** | no interpreter |
| Lint (ruff/flake8) / format check | n/a — no config present either | **BLOCKED** + **NOT CONFIGURED** | no interpreter; project ships no linter config |
| Type check (mypy/pyright) | n/a — no config present | **BLOCKED** + **NOT CONFIGURED** | same |
| Dependency vulnerability scan | `pip-audit` | **BLOCKED** | no pip |
| Docker build / compose stack | `docker compose up` | **BLOCKED** + **structurally impossible** | no Docker; Dockerfile missing (BUG-02) |
| Live RTSP validation | physical camera | **NOT POSSIBLE** | no camera (README admits) |
| GPU inference benchmark | GPU + model weights | **NOT POSSIBLE** | no GPU; weights deliberately not shipped |
| PostgreSQL behavior (JSON/TEXT mismatch symptom) | scratch Postgres | **BLOCKED** | no server/interpreter |
| Redis/Celery end-to-end | broker + worker | **BLOCKED** | no interpreter; worker start script missing |

## 3. Static assessment of the test suite (what it would verify)

Fixtures (`tests/conftest.py`): per-run temp SQLite file; env pinned **before** any `app.*` import; `fakeredis` replaces both `get_redis` and `get_rpc_redis`; Celery `send_task`/`revoke` stubbed; enrollment `_embed_photos` stubbed with deterministic vectors; `CONFIRMATION_FRAMES=1`, `FACE_QUALITY_ENABLED=false`, `CAMERA_SLOTS=2`, `AUTH_REQUIRED=true`, `admin/admin`.

| Module | Tests | Covers | Weaknesses |
|---|---|---|---|
| test_quality | 14 (18 items) | photo gates, dup registration, encryption round trip, bulk import | `infer_frames` stubbed — engine integration untested |
| test_auth_users | 27 | token, register/approve/reject/suspend, password requests, RBAC | strongest module |
| test_form_pipeline | 33 | formcsv parsing/selection + confirm/evalmetrics | exercises **disconnected** modules |
| test_judge_demo | 23 | judge_demo CLI | heavy monkeypatching; **masks BUG-03** |
| test_api | 15 | general API smoke, `/` returns HTML | — |
| test_attendance | 9 | session state machine, summaries | — |
| test_regression | 8 | encrypt→store→decrypt→match chain | guards the historical break |
| test_matching | 6 | threshold/margin/dup rules | — |
| test_periods | 4 | period CRUD + summaries | — |
| test_multisubject | 3 | multi-student index behavior | — |
| test_alerts | 3 | alert creation/dedup | monkeypatches `index.match` |
| test_registration | 2 | **tests a locally defined regex; never imports app code** | vacuous (H-03) |
| test_pipeline_e2e | 1 | "simulates" worker by writing logs directly; skips without `tests/assets/test_multi.mp4` (deliberately unshipped) | does not exercise `camera_task` (H-02) |

**Structural conclusion:** the suite is broad on the domain layer but has **zero direct coverage** of `app/tasks.py`, `inference/server.py`, `inference/client.py` timeout semantics, `frame_reader.py`, and `camera_task.py`'s loop — exactly the components where the most severe latent bugs were found (BUG-11, BUG-14, BUG-15). This is consistent with those bugs surviving a green suite.

## 4. Build/packaging results

| Artifact | Status |
|---|---|
| `pyproject.toml` | Present; setuptools backend; pytest `-q`; **conflicts with requirements.txt** on `celery[redis]` (H-05) |
| `requirements.txt` | Present, fully pinned, well-commented (incl. GPU swap recipe); authoritative |
| `requirements-dev.txt` | **MISSING** (referenced by README:90) |
| `Dockerfile` | **MISSING** (referenced by README + compose build) |
| `docker-compose.yml` | Present and internally consistent except: missing build context, missing `scripts/run_worker.sh`, missing `samples/videos/lecture.mp4`, default DB password |
| CI/CD | **NONE** (no `.github/`, no workflow files) |
| Lint/type configs | **NONE** |
| `.gitignore` / `.env.example` | **MISSING** (both referenced by docs) |

## 5. Honest claims policy for this audit

- No test is claimed to have passed; none was executed.
- "COMPLETE" statuses elsewhere in this report mean *statically complete and internally consistent*, backed by file:line evidence.
- Deterministic code-level defects (BUG-01 type confusion, BUG-02 missing files, BUG-03 signature mismatch, BUG-04 migration mismatch, BUG-05 JS logic) do not require execution to be confirmed.
- Anything dependent on real services (Redis, Celery, PostgreSQL, GPU, RTSP) is labeled UNVERIFIED throughout.
