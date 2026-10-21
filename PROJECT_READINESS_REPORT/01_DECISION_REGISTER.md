# 01 — Decision Register

Every value in this system that encodes institutional policy or deployment
policy, its current configuration, and its decision status. Decisions were put
to the project owner explicitly (nothing was changed silently).

## Resolved this phase (owner-approved)

| # | Item | Was | Now | Owner decision | Effect |
|---|---|---|---|---|---|
| D-1 | Frame-confirmation rule | config `2 frames / 5 s` vs CONTRIBUTING acceptance `3 frames / 10 s` vs disconnected `app/confirm.py` 3/10 | **`3 frames / 10 s`** everywhere that matters (`app/config.py` defaults, `.env.example`) | "Adopt 3 frames / 10 s" | recognition needs 3 confirmed matches within 10 s before a state change; CONTRIBUTING acceptance criteria are now truthful; `app/confirm.py` matches by coincidence (still disconnected — see open item O-1) |
| D-2 | Quality-gate coverage | gates on enroll + bulk-import only | gates on **enroll, add-photos, replace-face** (+ bulk per-row) | "Gate add/replace too" | no face data can enter the gallery without passing the per-photo gate; outages during the gate surface as 503, never a misleading 400 |
| D-3 | Grace windows | `ABSENT=15 / LATE=5 / EARLY_EXIT=10` min (formerly hard-coded) | **confirmed as-is** | "Confirm 15 / 5 / 10" | values are institutional defaults, still per-deployment configurable |
| D-4 | Script credentials | `check_accuracy.py` fell back to admin/admin | explicit `ADMIN_USERNAME`/`ADMIN_PASSWORD` required, else exit 2 | "Require explicit creds" | SEC-13 closed for the accuracy script |
| D-5 | GPU enablement | `onnxruntime` (CPU) only | `onnxruntime-gpu==1.26.0` + CUDA-12 wheels; **CUDA session verified active** on the RTX 5060 | "Attempt GPU enablement now" | GPU inference validated natively; compose `ORT_GPU_VERSION` default (1.26.0) matches the verified pin |
| D-6 | Starlette CVEs (14 advisories, blocked by fastapi 0.115.0 pin) | vulnerable transitive pin | `fastapi==0.143.1` + `starlette==1.8.0`, **0 advisories**, 195 tests green | "Attempt coordinated upgrade now" | `pip_audit -r requirements.txt` → *No known vulnerabilities found* |

## Open items still needing an owner/university decision

| # | Item | Current configuration | Impact if left | Decision needed |
|---|---|---|---|---|
| O-1 | `app/confirm.py`, `formcsv.py`, `evalmetrics.py` remain **disconnected** script-side modules with constants that diverge from `config.py` (sharpness 40 vs 30, brightness max 225 vs 230) | untouched | confusion about which quality constants are authoritative; the API path uses `config.py` | delete them, or unify them onto `config.py` |
| O-2 | Recognition threshold trio `RECOGNITION_THRESHOLD=0.40 / MARGIN=0.10 / DUPLICATE_IDENTITY_SIM=0.90` | shipped defaults (originally measured with the missing `eval/` toolkit) | these decide *who counts as who*; correctness at deploy-time depends on the deployment's own calibration | re-calibrate on the real enrolled cohort with `scripts/check_accuracy.py` and sign off the values |
| O-3 | Alert policy values `ALERT_LOW_CONFIDENCE=0.55`, `ALERT_DEDUP_WINDOW_S=300` | shipped defaults | controls alert volume vs. missed low-confidence matches | confirm or adjust |
| O-4 | Password-reset abuse controls (SEC-12) | flow exists, no per-account cap yet | burst abuse of reset requests on a shared deployment | approve the cap (e.g. N/hour/account) and implement in the next hardening pass |
| O-5 | Authz granularity | only `super_admin` exists as a role in practice | any future staff/teacher accounts need a real permission model | decide the role model before onboarding non-admin users |

No technical work in this phase was blocked on O-1…O-5; they are recorded so
they cannot be forgotten before go-live.
