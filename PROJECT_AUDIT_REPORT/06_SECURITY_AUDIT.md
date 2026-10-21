# 06 — Security Audit

Static code review only; no exploitation attempted, no secrets printed. Where a finding's exploitability depends on runtime conditions it is labeled SUSPECTED. Severity: CRITICAL / HIGH / MEDIUM / LOW / INFORMATIONAL. Confidence: HIGH (unambiguous code evidence) / MEDIUM / LOW.

## Findings

### SEC-01 — HIGH · Confidence HIGH · PII leak in tracked files
- **Evidence:** `test_drive.py:10` and `test_drive2.py:10` contain a real person's name and registration number in comments (verified by grep; values deliberately omitted from this report).
- **Impact:** The submission violates its own mandatory privacy rule (CONTRIBUTING: "never put a real registration number or name in a tracked file"; archive PII scan "must print 0"). Anyone receiving this archive is exposed to a data-protection violation; the individual is identifiable.
- **Mitigation:** Delete both files (scratch scripts referencing a nonexistent CSV — zero functionality loss). Add the PII scan step to CI before any future hand-off.

### SEC-02 — MEDIUM-HIGH · Confidence HIGH · Stored XSS via camera `rtsp_url` + JWT in localStorage
- **Evidence:** `app/static/app.js:421,429` interpolate `c.rtsp_url` unescaped into `innerHTML`; the adjacent `.replace(/^rtsp:\/\//, "rtsp://")` is a no-op. Backend accepts any string up to 500 chars (`schemas.py:190`). The SPA stores its JWT in `localStorage` (`app.js:54-58`), so injected script can exfiltrate it.
- **Impact:** An attacker who can create/update a camera (any authenticated admin) plants persistent script that steals every viewing admin's token. Privilege-persistence, not anonymous attack.
- **Mitigation:** escape `src` exactly as the `file_path` branch already does; validate `rtsp_url` format server-side (`rtsp://`/`rtsps://` scheme check). Consider HttpOnly cookie sessions or in-memory token handling long-term.
- **Verification:** register a camera with an HTML payload in `rtsp_url`; assert inert rendering.

### SEC-03 — MEDIUM-HIGH · Confidence HIGH · Enrolled reference photos served without authentication
- **Evidence:** `app/main.py:136-139` mounts `StaticFiles` at `/media` over the entire `DATA_DIR` with no auth dependency; the API returns relative photo paths (`StudentOut.photo_paths`), e.g. `students/<REG>/photo_0.jpg`.
- **Impact:** Anyone who can reach the port can fetch every enrolled student's face photos by iterating `REG` values (registration numbers are low-entropy, pattern-constrained to 6-12 alnum chars, and are exposed to any authenticated user). For a biometric-attendance product this exposes the raw identity corpus — the most privacy-sensitive asset in the system.
- **Mitigation:** move photo serving behind `get_current_admin` (a route handler streaming files), or serve from an authenticated path; consider not exposing photo paths at all.

### SEC-04 — MEDIUM · Confidence HIGH · Default credentials and default JWT secret
- **Evidence:** `config.py:50-51` (`admin`/`admin`), `config.py:47` (`jwt_secret="change-me-in-production"`). Production guards exist (`config.py:176-201`) but activate **only** when `ENVIRONMENT=production`; the shipped compose file never sets `ENVIRONMENT`, so `docker compose up` (had it worked) would run with defaults.
- **Impact:** Trivially guessable admin login and forgeable JWTs on any deployment that forgets the env var. The `.env` file that would carry overrides is itself missing from the archive.
- **Mitigation:** set `ENVIRONMENT=production` in compose; ship `.env.example` with instructions; consider failing startup when defaults are detected outside `development`.

### SEC-05 — MEDIUM · Confidence HIGH · No rate limiting or lockout on authentication
- **Evidence:** `POST /auth/token` (`auth.py:81-103`) has no throttle, backoff, or lockout; no middleware in `main.py` provides one. Password policy is length-only (≥8, `users.py:113-114`).
- **Impact:** online brute-force of admin accounts is unthrottled; combined with SEC-04 defaults this is severe on fresh installs.
- **Mitigation:** per-IP/per-user rate limit (e.g., slowapi or middleware), exponential backoff, lockout after N failures, and stronger password policy on registration.

### SEC-06 — MEDIUM · Confidence HIGH · JWT transmitted in WebSocket query string
- **Evidence:** `app/static/app.js:554` appends `?token=<jwt>`; server reads it (`ws.py:27`).
- **Impact:** tokens land in proxy/access logs, browser history, and `Referer`-adjacent contexts. Inherent to the chosen design (no header option in browser WS), but compounded by SEC-02.
- **Mitigation:** short-lived one-time WS tickets exchanged for the long-lived JWT, or `Sec-WebSocket-Protocol` token transport.

### SEC-07 — MEDIUM · Confidence HIGH · WS auth verifies signature only (revocation gap)
- **Evidence:** `ws.py:29-34` calls `decode_token` and never checks the user's DB status; REST re-checks per request (`security.py:118-120`).
- **Impact:** suspended/rejected/deleted users retain live surveillance-feed access until token expiry (default 12 h). No token revocation/blacklist exists anywhere; default expiry is 720 minutes (`config.py:49`).
- **Mitigation:** resolve the principal through `resolve_principal` in the WS handler before accept; add a revocation list (Redis) checked on decode.

### SEC-08 — MEDIUM · Confidence MEDIUM · Legacy-token fallback can reinstate deleted super admin
- **Evidence:** `security.py:113-116` — a signature-valid token with no DB row resolves by subject-name match; `subject == ADMIN_USERNAME` ⇒ `super_admin`, `security_level=5`.
- **Impact:** deleting the seeded admin user does not revoke previously issued tokens; any such token regains full super-admin power indefinitely (until JWT secret rotation).
- **Mitigation:** fail closed (401) when neither `uid` nor `subject` resolves to a DB user, or check a revocation epoch.

### SEC-09 — MEDIUM · Confidence MEDIUM · Bulk-import / photo path traversal potential (SUSPECTED)
- **Evidence:** `POST /students/bulk-import` accepts CSV rows whose `photo_path_or_folder` is resolved relative to `DATA_DIR` (`students.py:268-274`); no explicit normalization or `..` rejection was observed in `enrollment.bulk_enroll_from_csv` (`enrollment.py:219-472`).
- **Impact:** if `..` segments are honored, an authenticated admin could import (and thereby copy into `DATA_DIR`) arbitrary server-readable files — e.g., `.env` — which then also appear in `photo_paths` served via `/media` (SEC-03).
- **Confidence:** the join code was read but its normalization behavior is not runtime-verified; **treat as SUSPECTED until a fix phase confirms**.
- **Mitigation:** resolve the joined path and require `Path(data_dir).resolve()` as a strict prefix; reject absolute paths and `..`.

### SEC-10 — LOW-MEDIUM · Confidence HIGH · Unbounded upload sizes (DoS)
- **Evidence:** photo uploads (`students.py:66,184,214`) and CSV upload read the full body into memory (`photo.file.read()`, `await csv_file.read()`) with no size cap; no request-size middleware.
- **Impact:** an authenticated client can exhaust API memory/disk with multi-GB uploads.
- **Mitigation:** cap per-file and per-request bytes (e.g., 10 MB), stream to disk with limits.

### SEC-11 — LOW · Confidence HIGH · CORS defaults to `*`
- **Evidence:** `config.py:30` `cors_origins="*"`; `main.py:76-82` applies it with `allow_credentials=False` and all methods/headers.
- **Impact:** any website can make cross-origin calls to the API. Bearer-token auth (not cookies) limits impact to token-exfiltration scenarios; combined with XSS (SEC-02) the practical risk rises.
- **Mitigation:** default to same-origin/empty list in production; require explicit origins.

### SEC-12 — LOW · Confidence HIGH · Password-reset flow lacks abuse controls
- **Evidence:** `POST /auth/forgot-password` (`auth.py:153-177`) accepts unlimited unauthenticated requests, each writing a DB row; resolution is manual.
- **Impact:** unauthenticated DB-fill/spam; super admin's request queue can be flooded to mask a real request.
- **Mitigation:** rate-limit per username/IP; cap pending requests per username.

### SEC-13 — LOW · Confidence HIGH · Hardcoded credentials in scripts
- **Evidence:** `scripts/check_accuracy.py:38-39` (admin/admin), `scripts/smoke_test.py` default `ADMIN_PASSWORD=admin`.
- **Impact:** encourages credential reuse; would leak if defaults change to real values (as `check_accuracy.py`'s personal paths suggest this pattern already happened once with data — SEC-01).
- **Mitigation:** read from env only; delete stale scripts.

### SEC-14 — LOW · Confidence HIGH · Secret-management gaps in operations
- **Evidence:** encryption key rotation script does `os.chdir(ROOT)` (`rotate_encryption_key.py:31`) — minor, but a mutable-cwd operation on a security tool; `EMBEDDING_ENCRYPTION_KEY` is validated and production-hard-failed (good), but no `.env.example` exists to guide operators; `POSTGRES_PASSWORD` defaults to `attendance` in compose (`docker-compose.yml:35,68,108`).
- **Mitigation:** parameterize compose password with a required variable; add `.env.example`.

### SEC-15 — INFORMATIONAL · Positive controls observed
- PBKDF2-SHA256, 100k iterations, per-password random salt, constant-time compare, malformed-hash fail-closed, iteration-count bomb guard (`users.py:61-106`).
- Embeddings encrypted at rest (Fernet, key never derived from JWT secret — deliberate, documented in `config.py:183-201`).
- Non-enumerating forgot-password response; last-super-admin protection; self-registration cannot self-assign `super_admin` (`schemas.py:39`).
- `.env`, databases, photos, weights excluded by policy (though the `.gitignore` that would enforce this is itself missing from the archive).
- Global 500 handler logs server-side only; no stack traces leaked to clients (`main.py:125-133`).

### SEC-16 — INFORMATIONAL · Dependency vulnerability status: UNVERIFIED
No Python/pip/audit tooling exists in this environment; pinned versions (`requirements.txt`) could not be checked against CVE databases. Notably `insightface==2.0` pulls `onnxruntime` and `opencv-python` (GUI wheel in a server context — the file documents the headless swap for GPU images). Recommend `pip-audit`/`osv-scanner` in the fix phase.

### SEC-17 — INFORMATIONAL · Crypto design trade-offs (documented, accepted)
`crypto.py` uses a **fixed salt** for PBKDF2 key derivation (`:29`) → identical plaintexts produce identical ciphertexts (enables in-place key rotation; leaks equality of embedding sets across rows). 100k iterations is adequate but not state-of-the-art for 2026. Dev fallback key is a public constant with a startup warning — acceptable for non-production only.

## Summary table

| ID | Severity | Confidence | Area |
|---|---|---|---|
| SEC-01 | HIGH | HIGH | PII in tracked files |
| SEC-02 | MEDIUM-HIGH | HIGH | Stored XSS + localStorage JWT |
| SEC-03 | MEDIUM-HIGH | HIGH | Unauthenticated `/media` photo exposure |
| SEC-04 | MEDIUM | HIGH | Default admin/JWT secrets outside prod guard |
| SEC-05 | MEDIUM | HIGH | No auth rate limiting |
| SEC-06 | MEDIUM | HIGH | JWT in WS query string |
| SEC-07 | MEDIUM | HIGH | WS revocation gap (suspended users) |
| SEC-08 | MEDIUM | MEDIUM | Legacy-token super-admin revival |
| SEC-09 | MEDIUM | MEDIUM (suspected) | CSV path traversal |
| SEC-10 | LOW-MEDIUM | HIGH | Unbounded uploads |
| SEC-11 | LOW | HIGH | CORS `*` |
| SEC-12 | LOW | HIGH | Reset-request spam |
| SEC-13 | LOW | HIGH | Hardcoded creds in scripts |
| SEC-14 | LOW | HIGH | Compose default DB password; no `.env.example` |
| SEC-15..17 | INFO | — | Positive controls; unverified CVE status; crypto trade-offs |
