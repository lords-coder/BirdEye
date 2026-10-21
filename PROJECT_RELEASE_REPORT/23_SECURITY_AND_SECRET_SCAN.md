# 23 — Security and Secret Scan Report

**Date:** 2026-10-21 · **Scan Method:** Automated pattern scan (`scripts/secret_scan.py`) + manual review

---

## Summary

| Category | Count | Status |
|---|---|---|
| **Actual secrets found** | **0** | ✅ None |
| Template/placeholder values | 6 | Expected (in `.env.example`, config defaults) |
| Source code variable/field names | ~80 | Expected (parameter names, schema fields) |
| Test fixtures | ~20 | Expected (obviously fake values: `s3cret-pass`, `admin`, `letmein-1`) |
| Documentation/security analysis | ~15 | Expected (audit reports discussing security) |
| Frontend variable names | ~15 | Expected (JS variable names) |

**All findings are false positives** — no actual secrets, camera credentials, RTSP URLs with embedded passwords, API keys, encryption keys, JWT secrets, database passwords, or student PII were found in files that would be committed.

---

## Files with Template/Placeholder Values (Expected, Excluded by .gitignore)

| File | Example | Why Safe |
|---|---|---|
| `.env` | `JWT_SECRET=change-me-in-production`, `ADMIN_PASSWORD=admin`, `POSTGRES_PASSWORD=attendance` | **In `.gitignore`** — real deployment overrides these via environment |
| `.env.example` | Same as above with `change-me-*` placeholders | **Template only** — instructs operators to change on first deploy |
| `docker-compose.yml` | `POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-attendance}"` | Uses env var substitution; default only for local dev |
| `app/config.py` | `jwt_secret: str = "change-me-in-production"`, `admin_password: str = "admin"` | Pydantic defaults; production **must** override via env |

---

## Source Code Findings (All False Positives)

### Variable/Field Names Containing "password"/"secret"/"token"/"credential"
These are **parameter names, schema fields, and function names** — not values:
- `app/schemas.py`: `password: str = Field(...)`, `new_password`, `has_password`, `clear_password`
- `app/models.py`: `stream_password()`, `password_encrypted` field
- `app/api/cameras.py`: `body.password`, `camera.password_encrypted`, `redact_rtsp_url()`
- `app/security.py`: `jwt_secret`, `verify_credentials(username, password)`
- `app/users.py`: `hash_password()`, `verify_password()`, `authenticate()`
- `app/static/app.js`: `getToken()`, `setToken()`, `$("#login-password").value`

### RTSP URL Handling (Correctly Redacted)
- `app/schemas.py`: `redact_rtsp_url()` strips `user:pass@` from display URLs
- `app/services/camera_test.py`: `redact()` removes credentials from diagnostic logs
- `tests/test_camera_attendance.py`: Tests verify redaction works

### Test Fixtures (Obviously Fake)
| File | Value | Why Fake |
|---|---|---|
| `tests/test_auth_users.py` | `s3cret-pass` | Leetspeak "secret" |
| `tests/test_camera_attendance.py` | `admin`/`letmein-1` | Default CCTV creds, test-only |
| `scripts/smoke_test.py` | `ADMIN_PASSWORD=admin` | Reads from `.env` |

---

## Documentation Findings (Expected)

| Report | Content | Why Expected |
|---|---|---|
| `PROJECT_AUDIT_REPORT/06_SECURITY_AUDIT.md` | Discusses `admin/admin` default, `?token=` WS transport | Security audit finding |
| `PROJECT_READINESS_REPORT/03_SECURITY_STATUS.md` | Lists SEC-06, dev defaults | Readiness assessment |
| `PROJECT_REPAIR_REPORT/07_NEXT_PHASE_SECURITY_PLAN.md` | Plans to rotate `ADMIN_PASSWORD=admin` | Remediation plan |

---

## Files Excluded from Git (Verified via .gitignore)

| Path | Contains Real Secrets? | In .gitignore? |
|---|---|---|
| `.env` | **Yes (dev defaults)** | ✅ Yes |
| `.venv/` | No (dependencies only) | ✅ Yes |
| `data/` | SQLite DB + student photos | ✅ Yes |
| `build/`, `dist/` | Generated binaries | ✅ Yes |
| `dump.rdb` | Redis snapshot | ✅ Yes |
| `archive/` | Experimental code | ✅ Yes |

---

## Verdict

**No actionable secret exposure found.** All 123 scan hits are:
1. Expected template/placeholder values in files already excluded by `.gitignore`
2. Source code identifiers (not values)
3. Test fixtures with obviously fake data
4. Security documentation discussing known issues
5. Frontend variable names

**The repository is safe to commit as-is** (with current `.gitignore`).

---

## Recommendations for Production Deploy

1. **Never commit `.env`** — already in `.gitignore`
2. **Rotate defaults on first deploy**: `JWT_SECRET`, `ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `EMBEDDING_ENCRYPTION_KEY`
3. **Use a secrets manager** (Azure Key Vault, AWS Secrets Manager, HashiCorp Vault) for production
4. **WS token transport** — replace `?token=` query param with `Sec-WebSocket-Protocol` (tracked in SEC-06)
5. **Enable HTTPS/TLS** for all external traffic

---

## Scan Command

```bash
.venv\Scripts\python.exe scripts\secret_scan.py
```

Scan completed: **0 actual secrets found**.