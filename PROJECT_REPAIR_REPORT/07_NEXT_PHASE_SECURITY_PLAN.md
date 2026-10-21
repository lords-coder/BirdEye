# 07 — Next-Phase Security Plan

Status of all 17 audited SEC findings after this phase, then the prioritised
hardening plan. Only immediately exploitable defects were fixed now; the rest
is intentionally staged.

> **Readiness-phase update (2026-10-14):** P0-1 (dependency audit + full
> remediation, owner-approved), P0-2 (login throttling), P1-4 (production
> admin-password check), P1-5 (upload caps) and P1-6 (production CORS check)
> are **implemented and tested**; SEC-13 is closed for `check_accuracy.py`.
> Details and live-verification evidence:
> `PROJECT_READINESS_REPORT/03_SECURITY_STATUS.md`. Remaining open: SEC-06
> (WS token transport), SEC-02 residual (JWT in localStorage), SEC-12
> (reset-flow caps — needs a policy value), SEC-14 (secrets/ops).

## 1. Finding status after this phase

| ID | Severity | Status |
|---|---|---|
| SEC-01 PII leak in tracked files | HIGH | **FIXED** — PII files deleted; scans report 0 |
| SEC-02 Stored XSS via `rtsp_url` (+ JWT-in-localStorage) | M-H | **FIXED (sink)** — escaping + scheme validation. Residual: JWT still in localStorage → see P2 below |
| SEC-03 Reference photos served unauthenticated | M-H | **FIXED** — authenticated, confined `/media` route |
| SEC-04 Default credentials / default JWT secret | M | **FIXED** — production startup hard-fails on JWT secret, encryption key, **`ADMIN_PASSWORD=admin`** and **`CORS_ORIGINS='*'`** (readiness); `.env.example` annotates them |
| SEC-05 No rate limiting / lockout | M | **FIXED (login)** — Redis-backed per-(ip,user)+per-ip fixed window, 429 + Retry-After, fail-open on cache outage; reset-flow caps remain open (SEC-12) |
| SEC-06 JWT in WebSocket query string | M | Deferred → P2 |
| SEC-07 WS auth signature-only (revocation gap) | M | **FIXED** — full principal resolution; revoked/suspended/deleted → 4401 |
| SEC-08 Legacy-token fallback reinstates deleted super admin | M | **FIXED** — fail-closed 401 |
| SEC-09 Bulk-import path traversal (suspected) | M | **FIXED** — path confinement; RTSP schemes validated |
| SEC-10 Unbounded upload sizes | L-M | **FIXED** — `MAX_UPLOAD_BYTES` (25 MB) → 413 via ASGI middleware |
| SEC-11 CORS defaults to `*` | L | **FIXED in production** — `*` hard-fails startup; explicit allow-list required |
| SEC-12 Password-reset flow lacks abuse controls | L | Deferred → P0 (needs policy value, decision O-4) |
| SEC-13 Hardcoded credentials in scripts | L | **FIXED for `check_accuracy.py`** (explicit env required, exit 2); `test_cctv.py` retains documented dev defaults — sweep remaining scripts before deployment |
| SEC-14 Secret-management gaps in operations | L | Deferred → P3 |
| SEC-15 Positive controls | — | Preserved (JWT guards, RBAC, Fernet encryption, fail-fast production checks) |
| SEC-16 Dependency vulnerabilities UNVERIFIED | INFO | **CLOSED** — pip-audit run + remediated: python-multipart 0.0.31 (was 7 advisories), fastapi 0.143.1 + starlette 1.8.0 (was 14); final audit: **0 known vulnerabilities** (owner-approved upgrades) |
| SEC-17 Crypto design trade-offs | INFO | Accepted as documented (Fernet, fixed salt, 100k PBKDF2); revisit at P3 |

## 2. Prioritised plan

### P0 — before any shared/networked deployment

1. ~~**Dependency audit** (SEC-16)~~ **DONE 2026-10-14** — `pip-audit` in the
   venv, both fixes pinned in `requirements.txt` with rationale; re-audit
   clean. Re-run after every dependency change.
2. ~~**Rate limiting** (SEC-05, login)~~ **DONE** — `AUTH_RATE_LIMIT_PER_MINUTE`
   (default 10, 0 = off). Remaining: apply the same limiter to the
   password-reset endpoints (next item).
3. **Password-reset abuse controls** (SEC-12): cap requests per account/hour,
   uniform responses (already non-enumerating), and alert on bursts.
   *Needs the owner's cap value.*

### P1 — hardening pass (config + middleware, low risk)

4. ~~**Credentials** (SEC-04 residual)~~ **DONE** — production refuses to boot
   with `ADMIN_PASSWORD` at the shipped default.
5. ~~**Upload caps** (SEC-10)~~ **DONE** — `MAX_UPLOAD_BYTES`, 413 before parsing.
6. ~~**CORS** (SEC-11)~~ **DONE** — production requires an explicit allow-list.

### P2 — auth transport

7. **Move the JWT out of localStorage** (SEC-02 residual): httpOnly+SameSite
   cookie for the API, or keep bearer tokens but shorten `JWT_EXPIRE_MINUTES`
   and add a revoke list backed by Redis.
8. **WS token transport** (SEC-06): replace `?token=` with the
   `Sec-WebSocket-Protocol` bearer flow so tokens stop appearing in access
   logs. (Server-side dual-accept + non-visual `app.js` handshake change;
   schedule as a small dedicated task.)
9. ~~**Script credentials** (SEC-13, `check_accuracy.py`)~~ **DONE** — explicit
   env vars or exit 2.

### P3 — operations

10. **Secret management** (SEC-14): document vault/KMS wiring for
    `EMBEDDING_ENCRYPTION_KEY` + `JWT_SECRET`; extend
    `scripts/rotate_encryption_key.py` docs with a runbook.
11. **Crypto review** (SEC-17): consider random per-row KDF salt if
    ciphertext determinism ever matters; precompute the Fernet key once per
    process (100k PBKDF2 on every start).

## 3. Regression guard

All security fixes from this phase have tests in
`tests/test_repair_regressions.py` (fail-closed tokens, WS revocation, media
auth/traversal, RTSP scheme, bulk-import confinement) and
`tests/test_readiness_changes.py` (production CORS/admin-password checks,
upload cap, login throttle incl. fail-open, script-credential requirement).
Any future change that reintroduces one of these behaviours will fail CI.
