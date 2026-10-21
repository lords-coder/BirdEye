"""
BirdEye Native Launcher — uses pywebview with WebView2 backend.

Starts: Redis, GPU inference service, Celery worker, FastAPI
Opens: Native WebView2 window pointing to local API
"""

import sys
import os
import time
import subprocess
import threading
import urllib.request
import urllib.error
import atexit
import signal
import tempfile
from pathlib import Path

import webview

# Windows-specific constants for hiding console windows
if os.name == 'nt':
    CREATE_NO_WINDOW = 0x08000000
    SW_HIDE = 0
else:
    CREATE_NO_WINDOW = 0
    SW_HIDE = 0

# ---- startup logging & native error reporting (never raises) ----------------
# The packaged EXE is built with console=False, so print() output is invisible.
# Every startup phase and every fatal failure is therefore written to a bounded
# log file, and every unrecoverable failure also raises a native MessageBox
# (ctypes user32) that does not depend on WebView2 being functional.
import sys
from datetime import datetime

_LOG_DIR = Path(os.environ.get('LOCALAPPDATA', '')) / 'BirdEye' / 'logs'
_LOG_PATH = _LOG_DIR / 'launcher.log'
_LOG_MAX_BYTES = 1_000_000  # bounded: 1 MB, one .old backup kept


def _setup_logging() -> None:
    """Resolve a writable log location and rotate oversized logs. Never raises."""
    global _LOG_DIR, _LOG_PATH
    candidates = [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'BirdEye' / 'logs',
        Path(tempfile.gettempdir()) / 'BirdEye' / 'logs',  # bounded fallback
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / '.write_probe'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
            _LOG_DIR = candidate
            _LOG_PATH = candidate / 'launcher.log'
            break
        except Exception:
            continue
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            old = _LOG_PATH.with_suffix('.old')
            if old.exists():
                old.unlink()
            _LOG_PATH.replace(old)
    except Exception:
        pass


def _log(phase: str, message: str) -> None:
    """Append one timestamped, phase-tagged line. Never raises, never logs secrets."""
    try:
        with open(_LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} [{phase}] {message}\n")
    except Exception:
        pass


def _show_fatal_error(phase: str, message: str, exc: BaseException | None = None) -> None:
    """Record a fatal startup failure in the log and show a native error dialog."""
    if exc is not None:
        _log(phase, f"FATAL: {message} :: {type(exc).__name__}: {exc}")
        try:
            import traceback as _traceback
            _log(phase, _traceback.format_exc())
        except Exception:
            pass
    else:
        _log(phase, f"FATAL: {message}")
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return  # never block unit tests with a modal dialog
    try:
        import ctypes
        body = f"{message}\n\nStartup log:\n{_LOG_PATH}"
        ctypes.windll.user32.MessageBoxW(None, body, 'BirdEye — Startup Error', 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _detect_webview2_version() -> str | None:
    """Read-only discovery of the installed Evergreen WebView2 runtime version."""
    try:
        import winreg
        key_path = (r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
                    r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, 'pv')
            return str(value) if value and value != '0.0.0.0' else None
    except Exception:
        return None


def _focus_existing_window() -> None:
    """Best-effort focus of an already-running BirdEye window (single-instance UX)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, 'BirdEye — AI Video Attendance')
        if not hwnd:
            hwnd = user32.FindWindowW(None, 'BirdEye - Starting')
        if hwnd:
            user32.SetForegroundWindow(hwnd)
            _log('mutex', 'Focused existing BirdEye window')
    except Exception as exc:
        _log('mutex', f'Focus existing window failed: {exc}')


_original_excepthook = sys.excepthook


def _install_excepthooks() -> None:
    """Route unhandled main-thread and thread exceptions into the startup log."""
    def _hook(exc_type, exc, tb_obj):
        try:
            import traceback as _traceback
            _log('unhandled', ''.join(_traceback.format_exception(exc_type, exc, tb_obj)))
        except Exception:
            pass
        _original_excepthook(exc_type, exc, tb_obj)

    def _thread_hook(args):
        try:
            import traceback as _traceback
            _log('thread', ''.join(_traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


_setup_logging()
_log('init', f'Module loaded (frozen={bool(getattr(sys, "frozen", False))})')

# ---- Windows single-instance mutex ------------------------------------------
try:
    import ctypes
    _kernel32 = ctypes.windll.kernel32
    _MUTEX_NAME = "Global\\BirdEyeLauncherMutex"
    _mutex_handle = None
    _mutex_owned_by_us = False
    def _acquire_singleton_lock() -> bool:
        """Acquire a named mutex. Returns True only if this instance owns it.

        Cross-process semantics: if the named mutex already exists, another
        live process still holds an open handle to it (Windows destroys the
        object when the last handle closes, i.e. when the owner dies), so
        ERROR_ALREADY_EXISTS always means "another instance is running" and
        must NOT be treated as ownership.
        """
        global _mutex_handle, _mutex_owned_by_us
        if _mutex_owned_by_us:
            return False  # Already owned by this process
        _mutex_handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if not _mutex_handle:
            _log('mutex', f'CreateMutexW failed (GetLastError={_kernel32.GetLastError()})')
            return False
        last_error = _kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183 -> another live instance owns the mutex.
        if last_error == 183:
            _kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            _log('mutex', 'Rejected launch: another BirdEye instance already holds the singleton mutex')
            return False
        _mutex_owned_by_us = True
        _log('mutex', 'Singleton mutex acquired')
        return True
    def _release_singleton_lock() -> None:
        global _mutex_handle, _mutex_owned_by_us
        if _mutex_handle:
            _kernel32.ReleaseMutex(_mutex_handle)
            _kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
        _mutex_owned_by_us = False
except Exception:
    # Fallback if ctypes unavailable (should not happen on Windows)
    def _acquire_singleton_lock() -> bool:
        return True
    def _release_singleton_lock() -> None:
        pass

# ---- multiprocessing freeze support (required for PyInstaller on Windows) ---
import multiprocessing
multiprocessing.freeze_support()

# ---- configuration ---------------------------------------------------------
import sys

def _get_repo_root() -> Path:
    """Get the repository root directory, works both in source and frozen modes."""
    if getattr(sys, 'frozen', False):
        # When frozen, sys.executable points to the executable (BirdEye.exe)
        # The repository root is the parent of the directory containing the executable
        # In PyInstaller onefile mode, sys.executable is the exe itself
        # In PyInstaller onedir mode, sys.executable is the exe in the dist folder
        exe_path = Path(sys.executable).resolve()
        # Go up to find the repo root (look for known markers)
        for parent in [exe_path.parent, exe_path.parent.parent, exe_path.parent.parent.parent]:
            if (parent / "app").exists() and (parent / "scripts").exists():
                return parent
        # Fallback: assume exe is in dist/ or similar, go up two levels
        return exe_path.parent.parent
    else:
        return Path(__file__).resolve().parent.parent

def _get_python_executable() -> Path:
    """Get the path to the Python interpreter, works in both frozen and source modes."""
    if getattr(sys, 'frozen', False):
        # When frozen, sys.executable points to the frozen executable (BirdEye.exe)
        # We need to find the actual Python interpreter
        # In PyInstaller onedir mode, Python is typically at ../python.exe relative to the exe
        # In PyInstaller onefile mode, Python is embedded - we need to find it
        exe_path = Path(sys.executable).resolve()
        
        # Try to find python.exe relative to the executable
        # PyInstaller onedir: dist/BirdEye.exe, python.exe is at dist/python.exe
        # PyInstaller onefile: python is embedded, but we can use sys._MEIPASS
        if hasattr(sys, '_MEIPASS'):
            # Onefile mode - Python is extracted to _MEIPASS
            python_exe = Path(sys._MEIPASS) / "python.exe"
            if python_exe.exists():
                return python_exe
        
        # Try to find python.exe relative to the executable
        # PyInstaller onedir: dist/BirdEye.exe, python.exe is at dist/python.exe
        # PyInstaller onefile: python is embedded, but we can use sys._MEIPASS
        for base in [Path(sys.executable).resolve().parent, Path(sys.executable).resolve().parent.parent]:
            python_exe = base / "python.exe"
            if python_exe.exists():
                return python_exe
        
        # Fallback: try to find in the virtual environment
        # This assumes the venv is at the repo root
        repo_root = _get_repo_root()
        venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python
        
        # Last resort: try to find python in PATH
        import shutil
        python_in_path = shutil.which("python.exe")
        if python_in_path:
            return Path(python_in_path)
        
        # Ultimate fallback
        raise RuntimeError("Could not locate Python interpreter in frozen mode")
    else:
        return (Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe")


# ---- configuration ---------------------------------------------------------
import sys
REPO_ROOT = _get_repo_root()
VENV_PY = _get_python_executable()
API_HOST = "127.0.0.1"
API_PORT = 8000
BASE_URL = f"http://{API_HOST}:{API_PORT}"

# ---- service startup limits -------------------------------------------------
SERVICE_STARTUP_TIMEOUT = 30  # seconds per service
TOTAL_STARTUP_DEADLINE = 180  # total seconds for all services
MAX_RETRIES = 2
HEALTH_CHECK_INTERVAL = 2  # seconds

# ---- multiprocessing freeze support (required for PyInstaller on Windows) ---
import multiprocessing
multiprocessing.freeze_support()

# ---- service management ----------------------------------------------------
class ServiceManager:
    """Manages lifecycle of all BirdEye subprocesses with explicit ownership tracking."""

    def __init__(self):
        # Each entry: {"proc": Popen, "name": str, "cmd": list[str], "started_at": float}
        self.processes = []
        self._startup_order = ["redis", "inference", "worker", "api"]
        self._shutdown_started = False
        
        # Register cleanup handlers
        atexit.register(self.stop_all)
        signal.signal(signal.SIGTERM, lambda s, f: self.stop_all())
        signal.signal(signal.SIGINT, lambda s, f: self.stop_all())

    def _redis_ping(self):
        try:
            import redis
            r = redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=3)
            return r.ping()
        except Exception:
            return False

    def _check_inference(self):
        try:
            req = urllib.request.Request(f"{BASE_URL}/system/gpu-status", headers={"Authorization": "Bearer dummy"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 401)
        except urllib.error.HTTPError as e:
            return e.code in (200, 401, 403)
        except Exception:
            return False

    def _check_api(self):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/system/health", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _spawn(self, name: str, cmd: list[str], cwd: str, env: dict) -> subprocess.Popen:
        """Spawn a single subprocess with explicit command tracking."""
        print(f"[launcher] Starting {name}...")
        # Hide console window on Windows for background services
        creationflags = CREATE_NO_WINDOW if os.name == 'nt' else 0
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.processes.append({
            "proc": p,
            "name": name,
            "cmd": cmd,
            "started_at": time.time(),
        })
        print(f"[launcher] {name} started (PID {p.pid})")
        return p

    def _wait_for_process_ready(self, name: str, check_fn, timeout: int) -> bool:
        """Wait for a single service to pass its health check."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if check_fn():
                    print(f"[launcher] {name} healthy")
                    return True
            except Exception:
                pass
            time.sleep(HEALTH_CHECK_INTERVAL)
        print(f"[launcher] {name} did not become healthy within {timeout}s")
        return False

    def start_all(self):
        """Start all services in order, with per-service timeouts and rollback on failure."""
        print("[launcher] Starting services...")
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
        overall_deadline = time.time() + TOTAL_STARTUP_DEADLINE

        # 1. Redis
        redis_exe = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:\\Temp"))) / "opencode" / "redis" / "redis-server.exe"
        if not redis_exe.exists():
            for candidate in [Path("C:/Program Files/Redis/redis-server.exe"), Path("C:/Redis/redis-server.exe")]:
                if candidate.exists():
                    redis_exe = candidate
                    break
        
        if redis_exe.exists():
            self._spawn("redis", [str(redis_exe), "--port", "6379", "--appendonly", "no"], str(REPO_ROOT), env)
            if not self._wait_for_process_ready("redis", self._redis_ping, SERVICE_STARTUP_TIMEOUT):
                self.stop_all()
                raise RuntimeError("Redis failed to start")
        else:
            print("[launcher] WARNING: redis-server.exe not found, assuming external Redis")

        # 2. Inference service
        if time.time() > overall_deadline:
            self.stop_all()
            raise TimeoutError("Overall startup deadline exceeded before inference")
        self._spawn("inference", [str(VENV_PY), "-m", "app.inference.server"], str(REPO_ROOT), env)
        if not self._wait_for_process_ready("inference", self._check_inference, SERVICE_STARTUP_TIMEOUT):
            self.stop_all()
            raise RuntimeError("Inference service failed to start")

        # 3. Worker
        if time.time() > overall_deadline:
            self.stop_all()
            raise TimeoutError("Overall startup deadline exceeded before worker")
        worker_cmd = [
            str(VENV_PY), "-m", "celery", "-A", "app.workers.celery_app:celery_app", "worker",
            "--loglevel=info", "--hostname=worker@%h", "--pool=threads",
            "--concurrency=10", "--prefetch-multiplier=1",
            "--queues=control,jobs,camera_slot_0,camera_slot_1,camera_slot_2,camera_slot_3,camera_slot_4,camera_slot_5,camera_slot_6,camera_slot_7",
        ]
        self._spawn("worker", worker_cmd, str(REPO_ROOT), env)
        # Worker has no simple health check; give it a moment to bind queues
        time.sleep(5)
        
        # 4. API
        if time.time() > overall_deadline:
            self.stop_all()
            raise TimeoutError("Overall startup deadline exceeded before API")
        self._spawn("api", [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", API_HOST, "--port", str(API_PORT)], str(REPO_ROOT), env)
        if not self._wait_for_process_ready("api", self._check_api, SERVICE_STARTUP_TIMEOUT):
            self.stop_all()
            raise RuntimeError("API server failed to start")

    def wait_healthy(self, timeout=180):
        """Final verification that all services are healthy."""
        print("[launcher] Final health verification...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            all_ok = True
            for name, check in [("redis", self._redis_ping), ("inference", self._check_inference), ("api", self._check_api)]:
                try:
                    if not check():
                        all_ok = False
                        print(f"[launcher] Waiting for {name}...")
                except Exception:
                    all_ok = False
            if all_ok:
                print("[launcher] All services healthy!")
                return True
            time.sleep(HEALTH_CHECK_INTERVAL)
        print("[launcher] ERROR: Some services did not become healthy in time")
        return False

    def stop_all(self):
        """Gracefully stop all owned subprocesses, then force-terminate stragglers."""
        if self._shutdown_started:
            return  # idempotent
        self._shutdown_started = True
        
        print("[launcher] Stopping services...")
        # Stop in reverse startup order
        for entry in reversed(self.processes):
            p = entry["proc"]
            name = entry["name"]
            if p and p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=10)
                    print(f"[launcher] {name} stopped gracefully")
                except subprocess.TimeoutExpired:
                    p.kill()
                    print(f"[launcher] {name} force-killed after timeout")
                except Exception as e:
                    print(f"[launcher] {name} stop error: {e}")
        
        self.processes.clear()
        print("[launcher] All services stopped")


# ---- pywebview window ------------------------------------------------------
class BirdEyeApp:
    def __init__(self):
        self.window = None
        self.services = ServiceManager()
        self.shutdown_event = threading.Event()
        self.startup_status = "Starting BirdEye..."
        self.startup_complete = threading.Event()

    def _show_splash_screen(self):
        """Show a splash/loading screen while services start."""
        # Use a regular string (not f-string) to avoid conflicts with JS curly braces
        splash_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>BirdEye - Starting</title>
            <style>
                body {
                    margin: 0;
                    padding: 0;
                    background: #1a1a2e;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    color: #eaeaea;
                }
                .container {
                    text-align: center;
                    max-width: 400px;
                    padding: 2rem;
                }
                .logo {
                    font-size: 3rem;
                    margin-bottom: 1rem;
                }
                .title {
                    font-size: 1.5rem;
                    font-weight: 600;
                    margin-bottom: 0.5rem;
                    color: #fff;
                }
                .subtitle {
                    color: #8892b0;
                    margin-bottom: 2rem;
                    font-size: 1rem;
                }
                .progress-container {
                    width: 100%;
                    height: 4px;
                    background: #233554;
                    border-radius: 2px;
                    overflow: hidden;
                    margin-bottom: 1.5rem;
                }
                .progress-bar {
                    width: 0%;
                    height: 100%;
                    background: linear-gradient(90deg, #64ffda, #00b8a9);
                    border-radius: 2px;
                    transition: width 0.3s ease;
                    animation: pulse 2s infinite;
                }
                @keyframes pulse {
                    0% { opacity: 1; }
                    50% { opacity: 0.6; }
                    100% { opacity: 1; }
                }
                .status {
                    color: #8892b0;
                    font-size: 0.9rem;
                    min-height: 1.5em;
                }
                .steps {
                    margin-top: 2rem;
                    text-align: left;
                }
                .step {
                    display: flex;
                    align-items: center;
                    gap: 0.75rem;
                    padding: 0.5rem 0;
                    color: #8892b0;
                    font-size: 0.9rem;
                }
                .step.completed {
                    color: #64ffda;
                }
                .step.current {
                    color: #fff;
                }
                .step-icon {
                    width: 1.25rem;
                    height: 1.25rem;
                    flex-shrink: 0;
                }
                .spinner {
                    width: 1.25rem;
                    height: 1.25rem;
                    border: 2px solid #233554;
                    border-top-color: #64ffda;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">👁️</div>
                <h1 class="title">BirdEye</h1>
                <p class="subtitle">AI Video Attendance System</p>
                <div class="progress-container">
                    <div class="progress-bar" id="progressBar"></div>
                </div>
                <div class="status" id="statusText">Starting BirdEye...</div>
                <div class="steps" id="steps">
                    <div class="step" id="step-runtime">
                        <span class="step-icon">⏳</span>
                        <span>Checking local runtime</span>
                    </div>
                    <div class="step" id="step-db">
                        <span class="step-icon">⏳</span>
                        <span>Connecting to database</span>
                    </div>
                    <div class="step" id="step-inference">
                        <span class="step-icon">⏳</span>
                        <span>Initializing AI inference</span>
                    </div>
                    <div class="step" id="step-workers">
                        <span class="step-icon">⏳</span>
                        <span>Starting camera workers</span>
                    </div>
                    <div class="step" id="step-api">
                        <span class="step-icon">⏳</span>
                        <span>Starting API server</span>
                    </div>
                    <div class="step" id="step-ready">
                        <span class="step-icon">⏳</span>
                        <span>Ready to sign in</span>
                    </div>
                </div>
            </div>
            <script>
                const steps = [
                    {id: 'step-runtime', name: 'runtime'},
                    {id: 'step-db', name: 'db'},
                    {id: 'step-inference', name: 'inference'},
                    {id: 'step-workers', name: 'workers'},
                    {id: 'step-api', name: 'api'},
                    {id: 'step-ready', name: 'ready'}
                ];
                let currentStep = 0;
                
                function updateProgress(percent, status, completedStep) {
                    document.getElementById('progressBar').style.width = percent + '%';
                    document.getElementById('statusText').textContent = status;
                    
                    if (completedStep !== undefined) {
                        const stepEl = document.getElementById('step-' + completedStep);
                        if (stepEl) {
                            stepEl.classList.remove('current');
                            stepEl.classList.add('completed');
                            stepEl.querySelector('.step-icon').textContent = '✓';
                        }
                    }
                    
                    if (currentStep < steps.length) {
                        const nextStep = document.getElementById(steps[currentStep].id);
                        if (nextStep) {
                            nextStep.classList.add('current');
                            if (nextStep.querySelector('.step-icon').textContent === '⏳') {
                                nextStep.querySelector('.step-icon').className = 'step-icon spinner';
                            }
                        }
                    }
                }
                
                // Expose to pywebview
                window.pywebviewApi = {
                    updateProgress: updateProgress
                };
            </script>
        </body>
        </html>
        """
        # Create splash window
        splash_window = webview.create_window(
            "BirdEye - Starting",
            html=splash_html,
            width=500,
            height=600,
            resizable=False,
            frameless=True,
            easy_drag=False,
            on_top=True
        )
        
        # Expose API to JavaScript
        def update_progress(percent, status, completed_step):
            pass  # Handled by JS
        
        splash_window.expose(update_progress)
        return splash_window

    def run(self):
        _install_excepthooks()
        print("=" * 60)
        print("  BirdEye — AI Video Attendance")
        print("  Native Desktop Launcher (WebView2)")
        print("=" * 60)
        _log('entry', f'BirdEye launcher starting (frozen={bool(getattr(sys, "frozen", False))})')

        # ---- Single-instance mutex (Windows) ----
        if not _acquire_singleton_lock():
            print("[launcher] Another BirdEye instance is already running. Exiting.")
            _focus_existing_window()
            _show_fatal_error(
                'mutex',
                'BirdEye could not start because another instance is already running.',
            )
            return 1

        _log('config', f'REPO_ROOT={REPO_ROOT} VENV_PY={VENV_PY}')
        if not VENV_PY.exists():
            print(f"[launcher] ERROR: Venv python not found at {VENV_PY}")
            _show_fatal_error(
                'config',
                f'BirdEye could not load its application resources (missing {VENV_PY.name}). '
                'See the startup log.',
            )
            _release_singleton_lock()
            return 1

        os.chdir(REPO_ROOT)
        _log('config', 'Working directory set, configuration resolved')

        try:
            # ---- Backend services (unchanged startup/shutdown semantics) ----
            _log('services', 'Backend service startup started')
            print("[launcher] Starting services...")
            self.services.start_all()
            _log('services', 'Backend service startup complete, verifying health')

            if not self.services.wait_healthy():
                print("[launcher] Services failed to start. Check logs.")
                _log('services', 'Backend readiness verification FAILED')
                _show_fatal_error(
                    'services',
                    'BirdEye could not start the backend service. See the startup log.',
                )
                return 1
            _log('services', 'Backend readiness established')
            print(f"[launcher] API ready at {BASE_URL}")

            # ---- Desktop window: splash first, then the main window ----
            _log('webview', f'WebView2 runtime discovery: '
                            f'{_detect_webview2_version() or "version not detected"}')
            print("[launcher] Showing splash screen...")
            splash_window = self._show_splash_screen()
            _log('window', 'Splash window created (pre-GUI)')

            def update_splash_status(step_name, percent, status):
                """Update splash screen from the startup transition thread."""
                try:
                    splash_window.evaluate_js(
                        f"window.pywebviewApi.updateProgress({percent}, '{status}', '{step_name}')"
                    )
                except Exception as exc:
                    # Bounded, non-fatal: the splash may already be closing.
                    _log('window', f'splash update skipped: {type(exc).__name__}')

            def on_closing():
                print("[launcher] Window closing, shutting down services...")
                _log('entry', 'Main window closing: stopping owned services')
                self.services.stop_all()

            gui_failed = threading.Event()

            def open_main_window():
                """Runs inside the running GUI loop (spawned by webview.start).

                Creates the main window exactly once, then destroys the splash.
                Any failure is logged, reported via a native dialog, and ends the
                session by closing the splash — it never retries or recreates
                windows.
                """
                try:
                    if not splash_window.events.shown.wait(10):
                        raise RuntimeError('Splash window was not shown within 10s')
                    update_splash_status('ready', 90, 'Ready to sign in...')
                    time.sleep(0.3)  # let the final splash update render
                    _log('window', 'Creating main window (frontend navigation starts)')
                    print("[launcher] Opening BirdEye window...")
                    main_window = webview.create_window(
                        "BirdEye — AI Video Attendance",
                        BASE_URL,
                        width=1280,
                        height=800,
                        min_size=(1024, 768),
                        resizable=True,
                    )
                    if main_window is None:
                        raise RuntimeError('Main window creation was cancelled')
                    self.window = main_window
                    main_window.events.closing += on_closing
                    _log('window', 'Main window created; destroying splash')
                    splash_window.destroy()
                except Exception as exc:
                    gui_failed.set()
                    _show_fatal_error(
                        'window',
                        'BirdEye could not initialize the desktop runtime. See the startup log.',
                        exc,
                    )
                    _log('window', 'Startup transition failed; closing splash (no retry)')
                    try:
                        splash_window.destroy()
                    except Exception:
                        pass

            print("[launcher] Starting WebView2 event loop...")
            _log('window', 'WebView2 event loop starting (webview.start)')
            webview.start(func=open_main_window)
            _log('entry', 'GUI event loop finished')
            print("[launcher] Window closed")
            if gui_failed.is_set():
                return 1
            return 0

        except Exception as ex:
            print(f"[launcher] ERROR: {ex}")
            _show_fatal_error(
                'entry',
                'BirdEye encountered an unrecoverable startup error. See the startup log.',
                ex,
            )
            import traceback
            traceback.print_exc()
            return 1
        finally:
            _release_singleton_lock()
            self.services.stop_all()
            _log('entry', 'Launcher shut down')

if __name__ == "__main__":
    app = BirdEyeApp()
    sys.exit(app.run())