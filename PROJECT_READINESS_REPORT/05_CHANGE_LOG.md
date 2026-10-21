# 05 — Change Log (readiness phase)

Machine-readable record also appended to
`PROJECT_REPAIR_REPORT/08_CHANGE_MANIFEST.csv` (now 79 rows). No repair-phase
work was undone; the frontend was not touched.

## Source changes

| File | Change |
|---|---|
| `app/config.py` | confirmation defaults → **3 frames / 10 s** (D-1); new `max_upload_bytes` (25 MB) + `auth_rate_limit_per_minute` (10); production hard-checks for `CORS_ORIGINS='*'` and default `ADMIN_PASSWORD` |
| `app/main.py` | `BodySizeLimitMiddleware` (pure ASGI, 413 above `MAX_UPLOAD_BYTES`) |
| `app/api/auth.py` | `_login_rate_limited` (per-(ip,user) + per-ip fixed window, 429 + Retry-After, fail-open on Redis outage) wired into `POST /auth/token` |
| `app/services/enrollment.py` | shared `_quality_gate_or_raise`; quality gate now also covers `add_photos` + `replace_face` (D-2); enroll path consolidated onto the helper |
| `scripts/download_models.py` | **DEF-34**: mkdir `models/` before zip write; relocate/recover the official *flat* zip layout; no re-download when flat files already exist |
| `scripts/check_accuracy.py` | **SEC-13**: explicit credentials required, exit 2 otherwise |
| `requirements.txt` | `python-multipart==0.0.31`; `fastapi==0.143.1` + explicit `starlette==1.8.0` with rationale comment (owner-approved) |
| `docker-compose.yml` | **DEF-35**: inference service runs `download_models.py` before the server |
| `Dockerfile` | **DEF-35**: `/models` created + chowned to `appuser` (named-volume copy-up ownership) |
| `.env.example` | new security knobs + production notes |
| `README.md` | config-table rows for the new settings; CORS/ADMIN rows note the production hard-checks |

## New files

| File | Purpose |
|---|---|
| `scripts/validate_inference.py` | real-model canary (detect/embed + ACTIVE-provider evidence) |
| `scripts/validate_compose.py` | static compose/Dockerfile consistency validator |
| `scripts/validate_e2e.py` | end-to-end acceptance driver (gate, duplicate, camera, WS, summary, cleanup) |
| `tests/test_readiness_changes.py` | 16 regressions for D-1/D-2, SEC-04/05/10/13, DEF-34 |
| `PROJECT_READINESS_REPORT/00–05` | this report |

## Test changes

* `tests/test_quality.py` — production-startup test extended to assert the two
  new hard-checks (CORS wildcard, default admin password) alongside the
  existing encryption-key assertion. No assertion was weakened; the scenario
  grew stricter.

## Environment-level changes (not in the repo tree)

* `.venv`: `onnxruntime` → `onnxruntime-gpu==1.26.0`; `nvidia-{cuda-runtime,
  cublas,cudnn,cufft,curand,cusparse}-cu12` wheels; `pip-audit` (dev tool);
  `fastapi`/`starlette` upgrades above.
* `~/.insightface/models/buffalo_l` — 340 MB of weights (gitignored; fetched
  by the fixed `download_models.py`).
* `%TEMP%\opencode\redis` — portable Redis 5.0.14.1 used only for validation.
* Validation data (2 students, 3 cameras, 1 period, sessions/logs) created
  **and fully removed** via the API; `data/attendance.db` again contains only
  the seed super-admin.
* Local (gitignored) `.env`: stale `CONFIRMATION_FRAMES=2` /
  `CONFIRMATION_WINDOW_S=5.0` lines (a copy of the pre-decision
  `.env.example`) aligned to the approved 3 / 10.0; the confirming E2E run
  was executed with the new rule active.
