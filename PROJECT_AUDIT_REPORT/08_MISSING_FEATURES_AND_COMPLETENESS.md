# 08 — Missing Features and Completeness

Status values: COMPLETE · PARTIAL · PLACEHOLDER · DISCONNECTED · BROKEN · MISSING · UNVERIFIED.

"Completeness" is judged against (a) the project's own README capability table (§1), (b) documented API surface (README §7), (c) code-to-code integration evidence, and (d) the audit's defect list. **Nothing is COMPLETE merely because a file exists.**

## 1. README capability table vs reality

| # | README claim | README status | Audit status | Evidence |
|---|---|---|---|---|
| 1 | Enrollment: CSV bulk + API, quality gates, dup rejection, encrypt-at-rest | BUILT | **PARTIAL** | Enroll/bulk/clear/delete work statically; **`POST /students/{id}/photos` is BROKEN** (BUG-01); `PUT /students/{id}/face` skips dup-face check (BUG-29); engine-side quality gate omits pose/width/det-score on the live path; cannot be executed here |
| 2 | Recognition & attendance: detect→embed→match, sessions, summaries | BUILT | **PARTIAL (statically complete, runtime UNVERIFIED)** | Full chain implemented and unit-tested in isolation; the tests were not runnable here; camera loop has BUG-15/16/21/22 |
| 3 | Real-time multi-camera pipeline | PARTIAL | **PARTIAL — confirmed** | Matches README honesty: code complete, never validated on live RTSP; additionally BUG-11 (batch cap counts requests), BUG-13 (inprocess mode unwired), BUG-26 (global RTSP transport) |
| 4 | Deployment & operations: one-command setup, Compose, JWT, key hard-fail | PARTIAL | **BROKEN** | No Dockerfile; no `run_worker.sh`; no `setup_dev.*`; no `.env.example`; no Makefile; compose cannot build or start the worker (BUG-02). The *code-level* guards (key hard-fail, JWT checks) are sound |
| 5 | Operations workflow: alerts built; review queue, disputes, RBAC, retention on roadmap | ROADMAP | **PARTIAL / MISSING as stated** | Alerts: built (API complete, no UI). RBAC: partially real (super_admin/admin + security_level; but only 2 roles, `security_level` is stored and never enforced on any route). Review queue, attendance disputes, data-retention: **MISSING** (no code) |

## 2. Feature-by-feature checklist

### Authentication & users
| Feature | Status | Evidence |
|---|---|---|
| JWT login, 12 h expiry | COMPLETE | `auth.py:81-103` |
| Self-registration → pending approval | COMPLETE | `auth.py:106-150` |
| Super-admin user management (approve/reject/suspend/credential change, last-admin guard) | COMPLETE | `auth.py:209-327` |
| In-app password-reset queue | COMPLETE | `auth.py:153-177,330-388` |
| PBKDF2 hashing, .env re-sync | COMPLETE | `users.py:68-106,213-226` |
| Token revocation / logout invalidation | **MISSING** | no blacklist; WS ignores status (BUG-24); deleted-user token revival (BUG-28/SEC-08) |
| Rate limiting / lockout | **MISSING** | none anywhere (SEC-05) |
| `security_level` enforcement | **PLACEHOLDER** | stored (1-5), approved via UI, but **no route reads it for authorization decisions** |
| Email/SMS delivery of resets | **MISSING** (by design — manual queue) | — |

### Students & enrollment
| Feature | Status | Evidence |
|---|---|---|
| Enroll with 3-5 photos + per-photo gates | COMPLETE (static) | `students.py:54-95`, `enrollment.py:107-202` |
| List with search/section/pagination | COMPLETE | `students.py:98-133` (but perf concern, see 07/SUS-05) |
| Update student | COMPLETE (static) | `students.py:146-172` (no UI) |
| Add photos to existing student | **BROKEN** | BUG-01 |
| Replace face | PARTIAL | works but skips dup-face check (BUG-29); no UI |
| Clear face / delete student | COMPLETE | `students.py:235-243,301-309` |
| CSV bulk import with per-row report | COMPLETE (static) | `students.py:246-298`; path-traversal suspicion SEC-09; no UI |
| Photo quality gates (sharpness/brightness on API path) | PARTIAL | enforced in engine only; `quality.py` skips them despite settings existing (`quality.py:124-125`) |
| Duplicate-identity rejection | PARTIAL | implemented but threshold-inconsistent (BUG-17) |

### Cameras & pipeline
| Feature | Status | Evidence |
|---|---|---|
| Camera registry CRUD | COMPLETE (static) | `cameras.py`; latent 500 trap BUG-23; no UI for update |
| Start/stop pipeline with slot allocation | UNVERIFIED | needs Redis+Celery; logic reads sound (`cameras.py:209-340`) |
| RTSP + file sources, sampling, drop-oldest queue | UNVERIFIED | code complete; never validated live (README agrees); BUG-26 |
| Reconnect/backoff, heartbeat, unhealthy detection, reaper | UNVERIFIED | code complete; reaper swallows errors silently |
| Inference RPC + shared GPU service with cross-camera batching | PARTIAL | BUG-11 (batch cap semantics), BUG-14; runtime unverified |
| `INFERENCE_MODE=inprocess` debug mode | **DISCONNECTED** | BUG-13 — flag does nothing on the worker |
| Per-period deviation alerts (absent/late/early) | PARTIAL | built; hardcoded grace windows, TZ boundary bug (BUG-18), no UI |

### Attendance & reporting
| Feature | Status | Evidence |
|---|---|---|
| Session open/close state machine, dedup, midnight rollover | COMPLETE (static) | `services/attendance.py` |
| Daily summary, live view, audit log | COMPLETE | `api/attendance.py` |
| Per-period summary + per-student-in-period | COMPLETE (static) | no UI |
| Force-close session | COMPLETE (static) | no UI |
| Late/early/absent computation | COMPLETE (static) | `period_summary` |
| CSV/HTML report export | **DISCONNECTED** | exists only in `scripts/run_demo.py` / `judge_demo.py` (writes `eval/*`), not in the API/UI |
| Attendance disputes / review queue | **MISSING** | roadmap only |
| Data retention / purge jobs | **MISSING** | roadmap only (only `close_stale_sessions` exists) |

### Live feed (WS)
| Feature | Status | Evidence |
|---|---|---|
| Backlog replay + pub/sub streaming | COMPLETE | `ws.py:44-65` |
| Ping/pong | COMPLETE server-side; **no client ping** | `ws.py:64` |
| Auth | PARTIAL | signature-only (SEC-07/BUG-24); token in query (SEC-06) |
| Frontend reconnect | **BROKEN** | BUG-05/06 |

### Frontend coverage
| Feature | Status |
|---|---|
| Login/register/forgot, dashboard, students, cameras, attendance, live, users, system | COMPLETE (with defects BUG-05/06/07/08) |
| Periods management UI | **MISSING** |
| Alerts management UI | **MISSING** |
| Student edit / bulk import UI | **MISSING** |
| Camera edit UI | **MISSING** |
| Pagination UI (students, logs) | **MISSING** (hardcoded limit 200) |
| Period-attendance views | **MISSING** |

### DevOps & project infrastructure
| Item | Status |
|---|---|
| Dockerfile | **MISSING** |
| `scripts/run_worker.sh` | **MISSING** |
| `.env.example` | **MISSING** |
| `scripts/setup_dev.ps1/.sh`, `scripts/setup_gpu.*` | **MISSING** |
| `Makefile` | **MISSING** |
| `requirements-dev.txt` | **MISSING** |
| `eval/` (ROADMAP.md, calibration/diagnostic tools referenced throughout docs) | **MISSING** |
| `data/README.md`, `samples/videos/`, `samples/faces/` | **MISSING** |
| CI/CD, linter config, type-checker config | **MISSING** |
| Working `.gitignore` (referenced by CONTRIBUTING's privacy rules) | **MISSING** |
| Runnable test suite in this environment | **BLOCKED** (no Python) |

### Documentation accuracy
| Claim | Reality |
|---|---|
| "69 tests" (README:203) | 148 test functions |
| "Dockerfile + docker-compose.yml are provided" (README:337) | No Dockerfile |
| `GET /health` (README:252) | Route is `/system/health` |
| `WS /ws` (README:251) | Route is `/ws/live-feed` |
| `LOCAL_TIMEZONE` default `Asia/Kolkata` (README:273) | `config.py:133` default is `UTC` |
| Quick start "3 commands" (README §3) | setup script doesn't exist |
| `.gitignore` covers data (CONTRIBUTING:27) | no `.gitignore` in archive |
| `eval/ROADMAP.md` / `eval/PROGRESS.md` (README:10, CONTRIBUTING:39) | `eval/` doesn't exist |
| README §12 layout lists `eval/`, `data/README.md` | absent |
| Eval scripts `eval/enroll_and_calibrate.py`, `eval/diag_threshold.py` (README:345, ARCHITECTURE:78-79) | absent — the threshold-derivation reproduction commands cannot be run |

## 3. Summary counts

- **COMPLETE (static):** ~22 feature areas
- **PARTIAL:** ~12
- **BROKEN:** 3 core (`add photos`, WS reconnect, Docker/worker deployment) + several tool scripts
- **DISCONNECTED:** 4 modules (`confirm`, `formcsv`, `evalmetrics`, `inprocess` mode) + report export
- **MISSING:** ~16 items (disputes, retention, security_level enforcement, all missing UI areas, all DevOps artifacts)
- **UNVERIFIED:** entire runtime behavior (no execution possible)
