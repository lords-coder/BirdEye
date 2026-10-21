# 03 — Security Status

Position after the readiness phase, building on
`PROJECT_REPAIR_REPORT/07_NEXT_PHASE_SECURITY_PLAN.md` (statuses there are
updated in place). Only owner-approved or plan-authorized changes were made.

## 1. Fixed this phase (all with regression tests)

| Plan item | Implementation | Test |
|---|---|---|
| P0-1 / SEC-16 dependency audit | `pip-audit` installed as a dev tool; full triage: `python-multipart` 0.0.12→**0.0.31** (7 advisories), coordinated `fastapi` 0.115.0→**0.143.1** + explicit `starlette` **1.8.0** (14 advisories) — owner-approved. Re-audit: **`No known vulnerabilities found` (exit 0)** | the entire 195-test suite is the safety net for the framework upgrade; requirements pinned exactly with rationale comment |
| P0-2 / SEC-05 login throttling | `_login_rate_limited` in `app/api/auth.py`: Redis-backed fixed window per (ip, user) **and** per ip (3× limit), `AUTH_RATE_LIMIT_PER_MINUTE` (default 10, `0` disables), 429 + `Retry-After: 60`; **fails open on Redis outage** (an unavailable cache must never lock the admin out) | `test_login_rate_limit_triggers_429`, `..._disabled_with_zero`, `..._degrades_open_on_redis_outage` |
| P1-4 / SEC-04 default admin password | production startup now hard-fails while `ADMIN_PASSWORD` is `admin` or empty (same fail-fast philosophy as the JWT-secret and encryption-key checks) | `test_production_rejects_default_admin_password`, `test_production_passes_with_explicit_values` |
| P1-5 / SEC-10 upload caps | `MAX_UPLOAD_BYTES` (default 25 MB) enforced by a pure-ASGI `BodySizeLimitMiddleware` in front of every route → **413** before parsing | `test_oversized_body_rejected_413` |
| P1-6 / SEC-11 CORS wildcard | production startup hard-fails on `CORS_ORIGINS='*'`; explicit allow-list required | `test_production_rejects_wildcard_cors` |
| P2-10 / SEC-13 script credentials | `scripts/check_accuracy.py` requires explicit `ADMIN_USERNAME`/`ADMIN_PASSWORD` (exit 2 otherwise) | `test_check_accuracy_requires_explicit_credentials` |

Supporting docs updated: `.env.example` and the README config table document
every new knob and both production hard-checks.

## 2. Verified live on the running stack

* Unauthenticated `GET /students` → **401**; `GET /media/...` → **401**;
  bad credentials → **401**; good credentials → **200** (fastapi 0.143.1).
* Add-photos during an inference outage → **503** with an honest
  `inference service unavailable` detail (BUG-33 semantics hold live).
* Camera/attendance/WS flows require JWT end-to-end (the E2E driver
  authenticated every call; WS fed on a token-authenticated `/ws/live-feed`).

## 3. Still open (needs deployment config, hardware, or an owner decision)

| Item | Why not done here | Risk if shipped as-is |
|---|---|---|
| SEC-06 / P2-8 WS token transport (move off `?token=` to `Sec-WebSocket-Protocol`) | touches the browser handshake in `app/static/app.js`; deliberate frontend-integration change, out of this phase's "no frontend redesign" lane | tokens can appear in access logs / proxies |
| SEC-02 residual / P2-7 JWT in localStorage | same lane — needs the cookie-vs-shortened-token decision (O-items in the plan) | XSS can exfiltrate the token |
| SEC-12 password-reset abuse caps | policy value needed (requests per account/hour) — decision O-4 | reset-flow bursts |
| SEC-14 secret management (vault/KMS runbook) | operations-side; no vault exists here | secrets live in `.env` files |
| TLS / reverse-proxy hardening, backup strategy | deployment-environment work | plaintext transport, no recovery story |
| Authz granularity beyond `super_admin` | role model undecided (O-5) | no least-privilege for staff users |

## 4. Residual risk accepted for now

* Dev defaults (`admin`/`admin`, `JWT_SECRET=change-me…`) still boot **in
  development** by design; every production path now refuses them.
* `dev` `.env` on this machine holds the dev JWT secret — PyJWT's
  `InsecureKeyLengthWarning` is expected and the production check trips on the
  same default by design.
* Redis used for throttling runs unauthenticated on loopback in validation;
  compose `redis:7-alpine` likewise has no password — front production Redis
  with a bind/auth setting before networked deployment (SEC-14/ops item).
