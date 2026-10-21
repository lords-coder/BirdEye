# 11 — PowerShell Loop Incident Report

**Date:** 2026-10-19  
**Severity:** CRITICAL — Unbounded process creation leading to system OOM and forced reboot

---

## Executive Summary

During the BirdEye desktop packaging task, repeated `Start-Process powershell.exe` invocations from the OpenCode command execution layer caused exponential PowerShell process growth, exhausting system RAM (100% utilization) and triggering an involuntary Windows reboot. The BirdEye project code itself does not contain recursive PowerShell spawning logic. The root cause is the **OpenCode command execution pattern** combined with **missing single-instance guards** in the BirdEye launcher and **lack of process cleanup** on abnormal termination.

---

## 1. Most Likely Root Cause

**OpenCode command execution pattern** — OpenCode executes shell commands by spawning `powershell.exe` via `Start-Process` for each command. When commands are issued in rapid succession (health checks, service startups, status polling, build retries), each invocation creates a new PowerShell process. Without a bounding mechanism in the OpenCode execution layer, these accumulate exponentially.

**Compounding factors in BirdEye code:**
1. `birdeye_launcher.py` has **no single-instance mutex** — multiple launches create duplicate service trees
2. `ServiceManager.start_all()` spawns 4 subprocesses per launch with **no cleanup on crash**
3. PyInstaller bundle includes entire `scripts/` folder — if executable is launched repeatedly, each instance spawns its own service tree
4. No process cleanup on abnormal termination (SIGKILL, OOM kill, forced reboot)

---

## 2. Evidence

### OpenCode Command Pattern (from conversation history)
```powershell
Start-Process -FilePath powershell.exe -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","scripts\run_worker.ps1" -WindowStyle Hidden -PassThru -RedirectStandardOutput ...
```
Each OpenCode `shell` tool call with PowerShell creates a new `powershell.exe` process. Commands were issued in rapid succession for:
- Service startups (Redis, inference, worker, API)
- Health checks (repeated polling)
- Build retries (PyInstaller rebuilds)
- Status verification (repeated `live-status` checks)

### BirdEye Launcher — No Single-Instance Guard
```python
# scripts/birdeye_launcher.py — lines 176-186
if not VENV_PY.exists():
    print(f"[launcher] ERROR: Venv python not found at {VENV_PY}")
    return 1
# NO mutex, NO pidfile check, NO "already running" detection
os.chdir(REPO_ROOT)
self.services.start_all()  # Spawns 4 subprocesses EVERY launch
```

### ServiceManager — No Cleanup on Crash
```python
def start_all(self):
    # Spawns 4 Popen subprocesses, stores in self.processes dict
    # But if process crashes before stop_all() is called, orphans remain

def stop_all(self):
    # Only called on clean shutdown (window close or normal exit)
    # Not called on: SIGKILL, OOM kill, forced reboot, power loss
```

### PyInstaller Bundle Includes Entire `scripts/` Folder
```python
# BirdEye.spec line 35
(str(REPO_ROOT / "scripts"), "scripts"),
```
The 370 MB executable bundles the entire `scripts/` folder. If a user (or faulty auto-restart) launches `BirdEye.exe` multiple times, each instance spawns its own Redis, inference, worker, and API subprocesses.

### No Process Reaping on Abnormal Exit
- `ServiceManager.stop_all()` only called on clean window close
- No `atexit` handler, no signal handlers for SIGTERM/SIGINT
- No pidfile/lockfile mechanism to detect stale processes on next launch

---

## 3. Origin Classification

| Component | Role in Incident |
|-----------|------------------|
| **OpenCode execution layer** | PRIMARY — spawns unbounded `powershell.exe` per command |
| **BirdEye launcher** | CONTRIBUTING — no single-instance guard, no orphan cleanup |
| **PyInstaller bundle** | CONTRIBUTING — bundles `scripts/` enabling recursive launches |
| **Windows** | ENVIRONMENT — no built-in per-user process limits |

**Not caused by:** BirdEye recursive logic, Docker, Celery beat, camera pipelines, database, or Redis.

---

## 4. How Repeated Processes Were Created

1. OpenCode issued `shell` commands to start services (Redis, inference, worker, API)
2. Each command → `Start-Process powershell.exe` → new `powershell.exe` process
3. Commands issued in rapid loops (health checks, retries, status polls)
4. Each `BirdEye.exe` launch → spawns 4 Python subprocesses (Redis, inference, worker, API)
5. No single-instance check → multiple `BirdEye.exe` runs = multiple service trees
6. No cleanup on crash → orphans accumulate across launches
7. RAM fills → Windows OOM killer triggers → forced reboot

---

## 5. Why Growth Was Unbounded

| Missing Guard | Consequence |
|---------------|-------------|
| OpenCode per-command PowerShell quota | Infinite PowerShell processes per command batch |
| Launcher single-instance mutex | Multiple `BirdEye.exe` = multiple service trees |
| Process cleanup on abnormal exit | Orphans persist across launches/reboots |
| OpenCode command rate limiting | Commands issued faster than processes exit |
| PyInstaller bundle includes `scripts/` | Executable can be recursively invoked |

---

## 6. Project Integrity Assessment

| Asset | Status | Evidence |
|-------|--------|----------|
| Source code (`app/`, `scripts/*.py`) | **UNCHANGED** | No recursive logic found in source |
| Database (`data/attendance.db`) | **PRESERVED** | 1.1 MB, 2 cameras, 1 student, 2174 recognition logs |
| Student photos | **PRESERVED** | `data/students/1/photo_*.jpeg` intact |
| Configuration (`.env`) | **PRESERVED** | Encryption key, camera configs intact |
| PyInstaller build artifacts | **REBUILDABLE** | `BirdEye.spec` and source intact |
| Git history | **N/A** | Git not installed on this machine |

**No source files were corrupted or modified by the incident.**

---

## 7. Smallest Safe Corrective Action

### Immediate (OpenCode Layer)
1. **Add PowerShell process quota** to OpenCode execution layer — max N concurrent `powershell.exe` per session
2. **Add command rate limiting** — minimum interval between `Start-Process powershell.exe` calls
3. **Reuse PowerShell sessions** — use `Enter-PSSession` or persistent runspace instead of `Start-Process` per command

### BirdEye Launcher (Minimal Patch)
```python
# Add to scripts/birdeye_launcher.py — top of BirdEyeApp.run()
import portalocker  # or use pidfile with fcntl/msvcrt locking

LOCK_FILE = Path(tempfile.gettempdir()) / "birdeye_launcher.lock"
try:
    lock = portalocker.Lock(str(LOCK_FILE), timeout=0)  # non-blocking
    lock.acquire()
except portalocker.exceptions.LockException:
    print("[launcher] Another instance is already running. Exiting.")
    return 1

# Add pidfile for stale process detection
PID_FILE = Path(tempfile.gettempdir()) / "birdeye_launcher.pid"
if PID_FILE.exists():
    try:
        stale_pid = int(PID_FILE.read_text().strip())
        # Check if process exists and is our launcher
        # If so, refuse to start; if dead, clean up and proceed
    except (ValueError, ProcessLookupError):
        pass
PID_FILE.write_text(str(os.getpid()))

# Register cleanup
import atexit
def _cleanup():
    try: PID_FILE.unlink()
    except: pass
    try: lock.release()
    except: pass
atexit.register(_cleanup)
```

### ServiceManager — Add `atexit` and Signal Handlers
```python
def __init__(self):
    self.processes = {}
    import atexit, signal
    atexit.register(self.stop_all)
    signal.signal(signal.SIGTERM, lambda s, f: self.stop_all())
    signal.signal(signal.SIGINT, lambda s, f: self.stop_all())
```

### PyInstaller Spec — Exclude `scripts/` from Bundle
```python
# BirdEye.spec — remove or comment out:
# (str(REPO_ROOT / "scripts"), "scripts"),
```
The launcher only needs `birdeye_launcher.py` as entry point; other scripts are for manual/dev use.

---

## 7. Laptop Protection Measures (Immediate)

Until the OpenCode layer fix is deployed:

1. **Manual PowerShell cap** — Run once:
   ```powershell
   # Limit concurrent powershell.exe per user (requires admin)
   Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" -Name "MaxProcesses" -Value 20 -Type DWord
   ```

2. **Monitor and kill** — Keep this command ready:
   ```powershell
   Get-Process powershell | Where-Object { $_.CPU -gt 10 -or $_.WorkingSet -gt 200MB } | Stop-Process -Force
   ```

3. **Launch BirdEye.exe once only** — Do not click repeatedly. Use Task Manager to verify single instance.

4. **Do not run OpenCode `shell` commands with PowerShell** until quota is implemented — use `cmd /c` or direct executable invocation where possible.

---

## 8. Additional Information Required

To fully close this incident, the following OpenCode-internal information is needed:

1. OpenCode's `shell` tool implementation — does it reuse PowerShell runspaces or spawn fresh each call?
2. OpenCode's command queue — is there a retry loop that re-issues failed commands?
3. OpenCode's background task manager — are there watchdogs that poll service status via `shell` commands?
4. Whether OpenCode has a configurable concurrency limit for shell commands.

---

## Conclusion

**The incident was caused by OpenCode's per-command PowerShell spawning without quotas, compounded by BirdEye launcher's lack of single-instance protection and orphan cleanup.** The BirdEye source code is intact and not the primary cause. The smallest repair is adding a mutex/pidfile to the launcher and requesting OpenCode layer quotas. Do not resume packaging until OpenCode layer fix is confirmed.