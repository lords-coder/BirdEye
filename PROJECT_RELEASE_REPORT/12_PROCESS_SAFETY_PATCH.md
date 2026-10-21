# 12 — Process Safety Patch

**Date:** 2026-10-20  
**Scope:** Minimal safety changes to prevent unbounded process creation, enforce single-instance launch, bound service lifecycles, and remove recursive launch capability from the packaged executable.

---

## 1. Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `scripts/birdeye_launcher.py` | **Safety fix** | Added Windows named mutex for single-instance protection; restructured `ServiceManager` with explicit lifecycle tracking, per-service timeouts, overall startup deadline, retry limits, rollback on failure, graceful→force shutdown, atexit/signal handlers, and idempotent `stop_all`. |
| `BirdEye.spec` | **Packaging fix** | Removed `scripts/` directory from bundled datas to prevent recursive launches from the packaged executable. |
| `tests/test_process_safety.py` | **Test coverage** | Added 22 tests covering mutex, service lifecycle, startup deadlines, rollback, signal handlers, stop_all idempotence, spec safety, and recursive launch prevention. |

---

## 2. Root Causes Addressed

| Risk | Status | Mitigation |
|------|--------|------------|
| Multiple `BirdEye.exe` launches → duplicate service trees | **FIXED** | Windows named mutex (`Global\BirdEyeLauncherMutex`) acquired at startup; second launch exits with code 1. |
| Service subprocesses leaked on crash/OOM | **FIXED** | `atexit` + `SIGTERM`/`SIGINT` handlers call `stop_all()`; `stop_all()` is idempotent and safe to call multiple times. |
| Unbounded startup time | **FIXED** | Per-service timeout (30s), overall deadline (180s), startup rollback on any service failure. |
| No process ownership tracking | **FIXED** | Each spawned process recorded with name, cmd, start time; stopped in reverse order. |
| Recursive launch from packaged executable | **FIXED** | `scripts/` directory removed from PyInstaller bundle; executable cannot recursively invoke launch scripts. |
| PowerShell process leak from OpenCode | **NOT FIXED HERE** | Documented as external; requires OpenCode layer quota. |

---

## 3. File Changes

### 3.1 `scripts/birdeye_launcher.py`

**A. Windows Named Mutex (lines 19–40)**
```python
_MUTEX_NAME = "Global\\BirdEyeLauncherMutex"
_mutex_handle = None

def _acquire_singleton_lock() -> bool:
    _mutex_handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if not _mutex_handle: return False
    if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True

def _release_singleton_lock() -> None:
    if _mutex_handle:
        _kernel32.ReleaseMutex(_mutex_handle)
        _kernel32.CloseHandle(_mutex_handle)
```

**B. ServiceManager Restructure**
- `processes`: list of dicts with `{proc, name, cmd, started_at}` instead of dict
- `_spawn()` centralizes process creation with metadata
- Per-service `wait_for_process_ready(name, check_fn, timeout)`
- Overall startup deadline (`TOTAL_STARTUP_DEADLINE = 180s`)
- Rollback via `stop_all()` on any service failure
- `atexit` + `SIGTERM`/`SIGINT` handlers registered in `__init__`
- `stop_all()` is idempotent, stops in reverse order, graceful→force with 10s timeout
- Overall startup deadline checked before each service

**C. BirdEyeApp.run() Integration**
- Acquires mutex at start; releases in `finally` block
- Second launch exits with code 1 immediately
- `ServiceManager.stop_all()` called in `finally` + window close event

---

### 3.2 `BirdEye.spec`

**Removed from `datas`:**
```python
# REMOVED:
(str(REPO_ROOT / "scripts"), "scripts"),
```

**Rationale:** The packaged executable only needs `birdeye_launcher.py` as entry point. Bundling `scripts/` allowed recursive launches and exposed dev-only scripts (`run_worker.ps1`, `run_beat.ps1`, `setup_dev.ps1`, etc.) as runtime resources.

**Preserved runtime assets:**
- `app/static`, `app/api`, `app/inference`, `app/pipeline`, `app/services`
- All `app/*.py` modules, `app/workers`, `app/tasks.py`, `app/matching.py`, etc.
- `alembic/`, `alembic.ini`, `.env.example`, `requirements*.txt`

---

### 3.3 `tests/test_process_safety.py` (New File, 22 Tests)

| Test Class | Tests | Coverage |
|------------|-------|----------|
| `TestSingletonMutex` | 3 | Mutex acquire/release, idempotent release |
| `TestServiceManagerLifecycle` | 4 | Process metadata recording, stop_all idempotence, reverse-order stop |
| `TestServiceManagerStartup` | 5 | Deadline enforcement, rollback on Redis/Inference/API failure, overall deadline |
| `TestBirdeyeAppSingleInstance` | 2 | Second instance exits, first instance acquires/releases lock |
| `TestServiceManagerStopAll` | 3 | Graceful→force, graceful success, already-stopped |
| `TestServiceManagerSignalHandlers` | 2 | atexit and SIGTERM/SIGINT registration |
| `TestBirdeyeSpec` | 3 | scripts/ excluded, entry point correct, required assets included |

---

## 4. Test Results

```
294 passed, 1 warning in 47.30s
```
- 22 new process-safety tests pass
- All 272 existing tests still pass
- No regressions introduced

---

## 5. Verification Procedure (Staged)

| Stage | Action | Expected |
|-------|--------|----------|
| 1 | Launch `dist\BirdEye.exe` | Mutex acquired, services start, window opens |
| 2 | Launch second `dist\BirdEye.exe` | Exits with code 1, prints "Another BirdEye instance is already running" |
| 3 | Close first window | `stop_all()` called, all 4 services stopped in reverse order |
| 4 | Launch again after close | Starts cleanly, no orphan processes |
| 5 | Kill process mid-startup | `atexit`/`signal` handlers clean up subprocesses |
| 6 | Verify `scripts/` not in bundle | `scripts/` absent from `dist\` datas; no `.ps1` files in executable |

---

## 6. OpenCode External Dependency

**Unresolved:** OpenCode's `shell` tool spawns a new `powershell.exe` per command via `Start-Process`. This is an **external** process leak not fixable in BirdEye code.

**Workaround (documented in 11_POWERSHELL_LOOP_INCIDENT.md):**
```powershell
# Set per-user process limit (requires admin)
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" -Name "MaxProcesses" -Value 20 -Type DWord
```

**Required OpenCode fix:** Add per-session PowerShell process quota and command rate limiting in the OpenCode execution layer.

---

## 6. Remaining Blockers for Release

| Blocker | Status |
|---------|--------|
| OpenCode PowerShell quota | External — requires OpenCode change |
| Docker/WSL validation | Not started — requires Docker Desktop |
| Physical camera authentication | Not started — requires camera passwords |
| GPU inference in container | Not validated — requires NVIDIA Container Toolkit |

---

## 7. Conclusion

The process safety patch is **complete and tested**. The BirdEye launcher now:
1. Prevents duplicate launches via Windows mutex
2. Tracks and cleans up all owned subprocesses
2. Enforces startup deadlines with automatic rollback
3. Handles signals/atexit for cleanup on abnormal exit
4. Cannot recursively launch itself from the packaged executable

**All 294 tests pass.** The patch is minimal, reviewable, and ready for staged verification. Do not resume packaging until OpenCode PowerShell quota is implemented.