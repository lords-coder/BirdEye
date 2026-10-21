# Go-Live Checklist (07)

Do not declare SRM official-deployment readiness until every box is signed.

- [ ] Authenticated `connected` test on Classroom Exterior + Interior
  (password via UI only; stream paths recorded in camera Notes)
- [ ] Live preview verified per camera (interior sees classroom,
  exterior sees doorway)
- [ ] Walk-through scenarios pass (entry/exit/pass-through/both/
  lingering/return/overlap/disconnect) with review queue at zero
  unexplained
- [ ] 09:30–10:30 / 30-min lecture cases pass (32 PRESENT, 24 not,
  multi-interval merge, outside-window exclusion, in-progress
  provisional, unresolved-exit handling)
- [ ] Teacher override + audit verified on own class; cross-teacher 403
  re-verified on deployment data
- [ ] In-situ accuracy meets institutionally APPROVED thresholds
  (proposals in 02 — approval outstanding)
- [ ] Deployment runs on production topology (PostgreSQL, Redis,
  worker, GPU inference) with health green and restart drill passed
- [ ] Camera passwords rotated after chat exposure; stored only
  encrypted via UI
- [ ] O-1…O-5 owner sign-offs recorded
- [ ] Validation artifacts purged from the production database
