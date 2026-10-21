# 24 — GitHub Publication Report

**Date:** 2026-10-21 · **Target Repository:** `BirdEye` (private) · **Status:** Awaiting Git/GitHub CLI installation approval

---

## Prerequisites Status

| Tool | Installed | Version | Path | Action Required |
|---|---|---|---|---|
| `git` | ❌ No | — | Not in PATH | **Approval needed to install** |
| `gh` (GitHub CLI) | ❌ No | — | Not in PATH | **Approval needed to install** |

**Git and GitHub CLI are not installed on this machine.** Neither is in PATH nor found in common install locations (`C:\Program Files\Git`, `C:\Program Files\GitHub CLI`, user scoop shims).

---

## Local Repository Status

| Property | Value |
|---|---|
| Is Git repo? | No (no `.git` directory) |
| Has `.gitignore`? | Yes (updated with cleanup) |
| Staged changes? | N/A (no repo) |
| Untracked files? | All project files (first commit) |

---

## GitHub Remote Status

| Check | Result |
|---|---|
| GitHub CLI authenticated? | Unknown (CLI not installed) |
| Repository `BirdEye` exists under account? | Unknown (CLI not installed) |
| SSH key / PAT configured? | Unknown |

**Cannot verify** — requires GitHub CLI installation and authentication.

---

## Files Ready for Commit (Post-Cleanup)

| Category | Count | Examples |
|---|---|---|
| Backend source (`app/`) | ~25 files | `main.py`, `config.py`, `redis_client.py`, `api/cameras.py`, `pipeline/camera_task.py` |
| Frontend (`app/static/`) | 3 | `app.js`, `index.html`, `styles.css` |
| Tests | 23 | `test_*.py`, `conftest.py` |
| Migrations | 1 | `alembic/` |
| Config | 6 | `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `requirements*.txt`, `alembic.ini` |
| Packaging | 2 | `BirdEye.spec`, `Makefile` |
| Documentation | 10+ | `README.md`, `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`, `PROJECT_*_REPORT/` |
| Scripts | 8 | `scripts/secret_scan.py`, `scripts/profile_*.py` |
| Synthetic fixtures | 1 | `samples/videos/perf_baseline.mp4` |

**Total files to commit:** ~90 (excluding excluded dirs)

---

## Excluded from Commit (Verified by .gitignore)

| Path | Reason |
|---|---|
| `.env` | Real secrets (dev defaults) |
| `.venv/` | Virtual environment (~200 MB) |
| `data/` | SQLite DB + student photos |
| `build/`, `dist/` | Generated binaries (370 MB EXE) |
| `archive/` | Experimental frontend |
| `dump.rdb` | Redis snapshot |
| `__pycache__/`, `.pytest_cache/` | Python/tooling caches |

---

## Proposed Initial Commit

```bash
# After Git installation and repo init
git init
git checkout -b main
git add .
git commit -m "chore: initial commit - BirdEye AI Video Attendance for SRM University Haryana

- FastAPI backend with camera capture, face recognition, attendance
- Manus frontend (dark theme, live monitor, teacher overrides)
- OpenCV + Redis + ONNX Runtime GPU inference
- PostgreSQL + Redis + Celery worker architecture
- Camera lifecycle: bounded startup (30s), idempotent start, stale recovery
- Display decoupling: capture-thread tap at 15 FPS, change-driven MJPEG
- 342 tests passing (camera lifecycle, streaming, attendance, auth)
- 18 new streaming performance regression tests
"
```

---

## GitHub Repository Creation Plan

```bash
# After Git init and commit
gh repo create BirdEye --private --source=. --push
# Or if repo exists:
gh repo view BirdEye  # inspect first
git remote add origin https://github.com/<account>/BirdEye.git
git push -u origin main
```

**Required approvals before execution:**
1. ✅ Install `git` (from https://git-scm.com/download/win or winget)
2. ✅ Install `gh` (from https://cli.github.com/ or winget)
3. ✅ Run `gh auth login` (interactive browser flow — **never paste PAT in chat**)
4. ✅ Confirm `BirdEye` repo doesn't exist under another project
5. ✅ Approve pushing to the created/selected repository

---

## Documentation to Include in Repo

| File | Status |
|---|---|
| `README.md` | ✅ Exists (20 KB) |
| `CONTRIBUTING.md` | ✅ Exists (3.4 KB) |
| `.gitignore` | ✅ Updated |
| `.env.example` | ✅ Exists (sanitized template) |
| `docs/SETUP.md` | ⚠️ To create from existing docs |
| `docs/ARCHITECTURE.md` | ✅ Exists (7.9 KB) |
| `docs/DATABASE.md` | ⚠️ To create (schema, migrations, persistence) |
| `docs/TESTING.md` | ⚠️ To create (reproducible verification) |
| `docs/RELEASE.md` | ⚠️ To create (packaging, known requirements) |
| `docs/CAMERA_SETUP.md` | ⚠️ To create (safe camera config) |

---

## Required Approval Actions

| # | Action | Requester | Approver |
|---|---|---|---|
| 1 | Install Git for Windows | Assistant | **User** |
| 2 | Install GitHub CLI | Assistant | **User** |
| 3 | Run `gh auth login` | Assistant | **User** (interactive) |
| 4 | Verify `BirdEye` repo availability | Assistant | **User** |
| 5 | Push initial commit | Assistant | **User** |

---

## Post-Publication Verification

After push, verify:
- [ ] Remote URL correct: `git remote -v`
- [ ] Branch `main` on GitHub: `gh repo view BirdEye --json defaultBranchRef`
- [ ] Latest commit matches local: `git rev-parse HEAD` vs GitHub API
- [ ] Uploaded tree matches reviewed files: `gh api repos/<owner>/BirdEye/git/trees/main?recursive=1`
- [ ] No secrets in history: re-run `scripts/secret_scan.py` on cloned repo
- [ ] Tests pass in fresh clone: `git clone ... && cd BirdEye && .venv\Scripts\python.exe -m pytest`

---

## Blocker Summary

**Cannot proceed to publication without user approval to install Git and GitHub CLI.** All code, tests, cleanup, and security scans are complete and ready.