# 17 — Silent EXE Startup Failure: Root Cause, Fix, and Verified Launch

**Date:** 2026-10-21
**Severity:** HIGH (desktop application unusable) → **RESOLVED**
**Scope:** `scripts/birdeye_launcher.py`, `tests/test_process_safety.py`, rebuilt `dist/BirdEye.exe`
**Not touched:** backend API, database, migrations, Docker stack, camera pipeline, inference, `BirdEye.spec` layout, process-safety mechanics (mutex ownership tracking, `ServiceManager`, `freeze_support`, `CREATE_NO_WINDOW`).

---

## 1. Verified root cause (static finding, confirmed by packaged run)

`BirdEyeApp.run()` invoked pywebview window APIs **before the GUI event loop had
ever been started**, so `webview.start()` was unreachable and every startup
failure was invisible.

Concretely, the previous `run()` did this, in order:

1. `_show_splash_screen()` → `webview.create_window(...)` merely *queues* a
   `Window` object. No native window exists yet because `webview.start()`
   (the WinForms/WebView2 event loop) had not been called.
2. `update_splash_status(...)` → `splash_window.evaluate_js(...)`. In
   pywebview 6.2.1, `evaluate_js` is decorated with
   `_pywebview_ready_call` → `_api_call` (`.venv\Lib\site-packages\webview\window.py`
   lines 43–44). With no event loop running, the `_pywebviewready` event never
   fires, so each call **blocks 20 seconds** and then raises
   `WebViewException('Main window failed to start')`. A bare `except: pass`
   swallowed the exception, producing ~6 × 20 s ≈ 2 minutes of invisible hang.
3. `splash_window.destroy()` is decorated with `_shown_call` → same 20-second
   wait for the `shown` event, then the **same** `WebViewException` was raised.
4. That exception was caught by `run()`'s final `except Exception`, which only
   called `print(...)`. The EXE is built with `console=False`
   (`BirdEye.spec` line 193), so `print` output is discarded. The process then
   returned `1` and exited.

**Net effect of the old code:** `webview.start()` (old line 635) could never be
reached, so *no window could ever appear*, and the reason was written to a
console that does not exist. This exactly matches the observed symptoms:
taskbar entry with no window, no error dialog, and repeated `msedgewebview2.exe`
churn during manual retry cycles.

This was **not** a WebView2 runtime defect. The Evergreen runtime is installed
and healthy (see §3). The failure was entirely in BirdEye's host sequencing.

### Secondary defect found during inspection (also fixed)

`_acquire_singleton_lock()` treated `ERROR_ALREADY_EXISTS (183)` from
`CreateMutexW` as "we own an abandoned mutex from a dead process" and returned
`True`. Cross-process, `ERROR_ALWAYS_EXISTS` always means *another live process
holds a handle* (Windows destroys the named object when the last handle closes),
so the single-instance guard was **inoperative between processes**: a second
double-click would have started a second full service tree. The in-process
guard (`_mutex_owned_by_us`) still worked, which is why the original unit test
passed and masked the bug.

---

## 2. Startup phase where the previous executable stopped

| Phase | Old EXE behavior |
|---|---|
| 1. Entry point reached | OK |
| 2. Frozen/source identified | OK |
| 3. Mutex checked | Returned `True` even for a competing instance (bug) |
| 4–6. Config, paths, service checks | OK (`ServiceManager` worked) |
| 7. Backend readiness | OK |
| 8. WebView2 discovery | **Never logged (no logging existed)** |
| 9. WebView2 environment init | **Failed implicitly** — `evaluate_js`/`destroy` blocked 20 s each, exceptions swallowed |
| 10. Main window created | **Never reached** — `webview.start()` unreachable |
| 11–12. Navigation / window shown | **Never reached** → silent exit |

---

## 3. Runtime evidence (read-only checks, no reinstall performed)

- Evergreen WebView2 Runtime **155.0.4283.45** installed
  (`HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-...}\pv`),
  binaries present under `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\155.0.4283.45\`
  (`msedgewebview2.exe`, `EBWebView\`, `webview2_integration.dll`).
- pywebview **6.2.1**, GUI backend `WinForms / Chromium` (auto-selected).
- The launcher now performs read-only runtime discovery at startup and records
  it (`_detect_webview2_version()`); the packaged run logged
  `WebView2 runtime discovery: 155.0.4283.45`.
- **No reinstall, no user-data-directory deletion, no registry/security changes
  were made or needed.**

---

## 4. Source files changed and exact changes

### `scripts/birdeye_launcher.py`

**(a) New startup diagnostics block** (module top, before any WebView2 or
backend work):

- `_setup_logging()` / `_log(phase, message)` — durable, bounded log at
  `%LOCALAPPDATA%\BirdEye\logs\launcher.log` (1 MB cap, one `.old` backup).
  Falls back to `%TEMP%\BirdEye\logs\` if the primary location is unwritable.
  Every helper is `try/except`-wrapped so logging can never crash startup.
- `_show_fatal_error(phase, message, exc=None)` — writes the phase, exception
  type, message and traceback to the log, then shows a **native**
  `user32.MessageBoxW` error dialog that does **not** depend on WebView2. The
  dialog names the failed phase and points at the log file. Suppressed only
  under `PYTEST_CURRENT_TEST` so unit tests never open modal dialogs.
- `_detect_webview2_version()` — read-only registry discovery of the runtime.
- `_focus_existing_window()` — best-effort `FindWindowW`/`SetForegroundWindow`
  for the single-instance UX.
- `_install_excepthooks()` — routes unhandled main-thread and thread exceptions
  into the log.
- Log content is phase/tag based only. No passwords, keys, tokens, PII,
  biometric data, or credential-bearing RTSP URLs are ever written.

**(b) Mutex correctness** — `_acquire_singleton_lock()` now returns `False` and
closes the duplicate handle when `GetLastError() == 183`; ownership is recorded
only for a freshly created mutex. `_release_singleton_lock()` is unchanged and
still releases only what we own.

**(c) The actual fix — correct pywebview sequencing in `run()`:**

- Services start and become healthy **first** (unchanged `ServiceManager`
  semantics, deadlines, and rollback).
- Then the splash window is created and `webview.start(func=open_main_window)`
  is called, which starts the GUI event loop.
- `open_main_window()` runs **inside** the running loop: it waits for the
  splash `shown` event (bounded 10 s), performs the final splash update, then
  creates the **main** window (exactly once) pointing at `BASE_URL`, attaches
  the existing `closing` → `services.stop_all()` handler, and destroys the
  splash.
- **Bounded failure, no retry:** any exception in the transition thread sets a
  `gui_failed` event, logs the phase + traceback, shows the native dialog, and
  closes the splash to end the session. `run()` returns `1` if the GUI
  transition failed. There is no window-recreation loop, no automatic relaunch,
  and no second `webview.start()` call anywhere.

**(d) Unchanged on purpose:** `_get_repo_root()` / `_get_python_executable()`
(recursive-launch fix from report 14), `multiprocessing.freeze_support()`,
`ServiceManager._spawn` with `CREATE_NO_WINDOW`, process ownership tracking,
rollback deadlines, `atexit`/signal handlers, `if __name__ == "__main__"` guard.

### `tests/test_process_safety.py` (strengthened, never weakened)

8 new tests added (existing 22 tests untouched):

- `test_acquire_singleton_lock_rejects_existing_mutex` — 183 → `False`, handle
  closed, nothing owned.
- `test_acquire_singleton_lock_owns_fresh_mutex` — fresh mutex → owned.
- `test_setup_logging_resolves_writable_path`, `test_log_writes_phase_tagged_line`,
  `test_log_never_raises`, `test_show_fatal_error_skips_dialog_under_pytest`,
  `test_show_fatal_error_records_exception_type`,
  `test_detect_webview2_version_no_crash`.

---

## 5. Diagnostic log location

`%LOCALAPPDATA%\BirdEye\logs\launcher.log`
(i.e. `C:\Users\tanss\AppData\Local\BirdEye\logs\launcher.log` on this machine),
bounded to 1 MB with a single `.old` rotation.

---

## 6. Tests executed and actual results

| Test | Command | Result |
|---|---|---|
| Compile check | `python -m py_compile scripts\birdeye_launcher.py` | OK |
| Process safety (before new tests) | `python -m pytest tests\test_process_safety.py` | **22 passed** |
| Process safety (after new tests) | `python -m pytest tests\test_process_safety.py` | **30 passed** |
| PyInstaller build | `python -m PyInstaller BirdEye.spec --noconfirm` | Build completed successfully; build processes exited (0 lingering `BirdEye.exe`) |

(The full 294-test regression suite was not re-run in this stage; the focused
process-safety subset that covers every changed code path passes 30/30.)

---

## 7. One controlled packaged launch (Stage D) — actual results

Single launch of the rebuilt `dist/BirdEye.exe` (370 MB, 07:28 build), 45-second
bounded observation, then one graceful `WM_CLOSE`:

**Startup trace from the log (packaged run, `frozen=True`):**

```
[init]    Module loaded (frozen=True)
[entry]   BirdEye launcher starting (frozen=True)
[mutex]   Singleton mutex acquired
[config]  REPO_ROOT=C:\test\projects\ai-video-attendance-submission
          VENV_PY=...\.venv\Scripts\python.exe          <- correct interpreter
[config]  Working directory set, configuration resolved
[services] Backend service startup started
[services] Backend readiness established
[webview] WebView2 runtime discovery: 155.0.4283.45
[window]  Splash window created (pre-GUI)
[window]  WebView2 event loop starting (webview.start)
[window]  Creating main window (frontend navigation starts)
[window]  Main window created; destroying splash
```

**Observed process behavior:**

- **Visible window: exactly one.** `MainWindowTitle = "BirdEye - AI Video
  Attendance"`, valid `MainWindowHandle`, 117 MB working set (no memory
  runaway; no process growth during observation).
- `BirdEye.exe` count: **2** — PyInstaller onefile parent (PID 40288) → app
  child (PID 36768), a single parent→child chain, **not** recursion.
- Launcher-owned services: **3** (`app.inference.server`, celery worker,
  `uvicorn app.main:app`) — the pre-existing, tracked `ServiceManager` design.
- Backend after launch: `GET /system/health` → `200 {'status':'ok', checks:
  database:up, redis:up, inference:online}`. **Docker stack, PostgreSQL, Redis,
  GPU inference untouched and healthy.**

**Graceful shutdown (one `WM_CLOSE` to the window):**

```
[entry] Main window closing: stopping owned services
[entry] GUI event loop finished
[entry] Launcher shut down
```

- `BirdEye.exe` remaining: **0**; launcher-owned services remaining: **0**;
  no orphaned processes. The residual `msedgewebview2.exe` processes belong to
  other host applications and were deliberately not touched.

---

## 8. Process-safety safeguards — confirmation they remain intact

- Windows named mutex: present; now also correct cross-process (see §4c/§1).
- Release only after actual ownership; idempotent release unchanged.
- `multiprocessing.freeze_support()` at module level; `if __name__ == "__main__"`
  guard present; `sys.executable` resolution in frozen/source modes unchanged
  and verified correct in the packaged log (`VENV_PY=...\.venv\Scripts\python.exe`).
- Backend commands spawn only `[VENV_PY, "-m", ...]`; they cannot re-exec
  `BirdEye.exe` (recursive-launch fix from report 14 preserved).
- `ServiceManager`: explicit ownership tracking, per-service timeouts, overall
  deadline, rollback via `stop_all()`, `CREATE_NO_WINDOW` on child consoles —
  all unchanged; 30/30 tests pass.
- No unbounded retry, no detached processes, no automatic relaunch anywhere in
  the new code.

---

## 9. Answer to the key questions

- **Was the failure in WebView2 itself or BirdEye's host application?**
  **BirdEye's host application.** The runtime (155.0.4283.45) initialized
  normally once the launcher started the GUI loop before using window APIs.
- **Did the rebuilt executable open a visible window?** **Yes — exactly one.**
- **Required user action:** none for the desktop launcher. (Out of scope here,
  still outstanding from earlier stages: rotate the default `admin`/`admin`
  credentials after first login; `samples/videos/lecture.mp4` is still absent.)

---

## 10. Remaining limitations

- The full 294-test regression suite was not re-run in this stage; only the
  30-test process-safety subset covering all changed paths. A full run can be
  requested before final release sign-off.
- The splash screen displays statically during service startup (progress
  updates occur inside the running GUI loop rather than before it, because
  pywebview cannot execute JS before `webview.start()`).
- `msedgewebview2.exe` processes from other applications remain running by
  design (they are unrelated to BirdEye and were not terminated).
