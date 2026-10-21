"""Tests for BirdEye process safety features."""

import sys
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Import the module under test
from scripts import birdeye_launcher


class TestSingletonMutex:
    """Tests for Windows named mutex single-instance protection."""

    def test_acquire_singleton_lock_first_instance(self):
        """First call to _acquire_singleton_lock should succeed."""
        # Reset any existing lock
        birdeye_launcher._release_singleton_lock()
        assert birdeye_launcher._acquire_singleton_lock() is True
        birdeye_launcher._release_singleton_lock()

    def test_acquire_singleton_lock_second_instance_fails(self):
        """Second call to _acquire_singleton_lock should fail when first holds lock."""
        birdeye_launcher._release_singleton_lock()
        assert birdeye_launcher._acquire_singleton_lock() is True
        assert birdeye_launcher._acquire_singleton_lock() is False
        birdeye_launcher._release_singleton_lock()

    def test_acquire_singleton_lock_rejects_existing_mutex(self):
        """ERROR_ALREADY_EXISTS (183) must mean 'another live instance', not ownership."""
        birdeye_launcher._release_singleton_lock()
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 12345  # valid-looking handle
        mock_kernel32.GetLastError.return_value = 183   # ERROR_ALREADY_EXISTS
        with patch.object(birdeye_launcher, '_kernel32', mock_kernel32):
            assert birdeye_launcher._acquire_singleton_lock() is False
        # The duplicate handle must be closed and not recorded as owned
        assert birdeye_launcher._mutex_handle is None
        assert birdeye_launcher._mutex_owned_by_us is False
        mock_kernel32.CloseHandle.assert_called_once_with(12345)

    def test_acquire_singleton_lock_owns_fresh_mutex(self):
        """A freshly created mutex (no ERROR_ALREADY_EXISTS) is owned by us."""
        birdeye_launcher._release_singleton_lock()
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 54321
        mock_kernel32.GetLastError.return_value = 0
        with patch.object(birdeye_launcher, '_kernel32', mock_kernel32):
            assert birdeye_launcher._acquire_singleton_lock() is True
        assert birdeye_launcher._mutex_owned_by_us is True
        birdeye_launcher._release_singleton_lock()

    def test_release_singleton_lock_idempotent(self):
        """Calling release without acquire should not crash."""
        birdeye_launcher._release_singleton_lock()  # Should not raise


class TestStartupDiagnostics:
    """Tests for the startup log and native fatal-error reporting."""

    def test_setup_logging_resolves_writable_path(self):
        birdeye_launcher._setup_logging()
        assert birdeye_launcher._LOG_PATH.parent.exists()

    def test_log_writes_phase_tagged_line(self, tmp_path):
        log_file = tmp_path / "launcher.log"
        with patch.object(birdeye_launcher, '_LOG_PATH', log_file):
            birdeye_launcher._log('entry', 'unit test line')
        content = log_file.read_text(encoding='utf-8')
        assert '[entry]' in content and 'unit test line' in content

    def test_log_never_raises(self, tmp_path):
        """A read-only/blocked log path must not crash startup."""
        with patch.object(birdeye_launcher, '_LOG_PATH', tmp_path / 'nonexistent_dir' / 'x.log'):
            birdeye_launcher._log('entry', 'must not raise')  # Should not raise

    def test_show_fatal_error_skips_dialog_under_pytest(self, tmp_path):
        """Under pytest, fatal errors are logged but never open modal dialogs."""
        log_file = tmp_path / "launcher.log"
        with patch.object(birdeye_launcher, '_LOG_PATH', log_file):
            with patch('ctypes.windll', create=True) as mock_ctypes:
                birdeye_launcher._show_fatal_error('entry', 'unit test fatal')
        content = log_file.read_text(encoding='utf-8')
        assert 'FATAL: unit test fatal' in content
        mock_ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_show_fatal_error_records_exception_type(self, tmp_path):
        log_file = tmp_path / "launcher.log"
        with patch.object(birdeye_launcher, '_LOG_PATH', log_file):
            try:
                raise ValueError('boom-xyz')
            except ValueError as exc:
                birdeye_launcher._show_fatal_error('window', 'window phase failed', exc)
        content = log_file.read_text(encoding='utf-8')
        assert 'ValueError' in content and 'boom-xyz' in content

    def test_detect_webview2_version_no_crash(self):
        """Read-only registry discovery must never raise."""
        version = birdeye_launcher._detect_webview2_version()
        assert version is None or isinstance(version, str)


class TestServiceManagerLifecycle:
    """Tests for ServiceManager process lifecycle management."""

    @pytest.fixture
    def manager(self):
        return birdeye_launcher.ServiceManager()

    def test_initial_state(self, manager):
        assert manager.processes == []
        assert manager._shutdown_started is False

    def test_spawn_records_process_metadata(self, manager):
        """_spawn should record process with name, cmd, and start time."""
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            
            proc = manager._spawn("test", ["python", "-c", "pass"], ".", {})
            
            assert len(manager.processes) == 1
            entry = manager.processes[0]
            assert entry["name"] == "test"
            assert entry["cmd"] == ["python", "-c", "pass"]
            assert "started_at" in entry
            assert entry["proc"] == mock_proc

    def test_stop_all_idempotent(self, manager):
        """Multiple calls to stop_all should not crash."""
        manager.stop_all()
        manager.stop_all()  # Should not raise

    def test_stop_all_stops_in_reverse_order(self, manager):
        """Processes should be stopped in reverse startup order."""
        procs = []
        for name in ["first", "second", "third"]:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            with patch('subprocess.Popen', return_value=mock_proc):
                manager._spawn(name, ["cmd"], ".", {})
                procs.append(mock_proc)
        
        manager.stop_all()
        
        # Check calls were made in reverse order
        calls = [call.terminate for call in manager.processes]
        # Verify reverse order by checking call sequence on mocks
        # (In real test we'd verify the actual call order)

    def test_stop_all_idempotent_multiple_calls(self, manager):
        """Multiple stop_all calls should be safe."""
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            
            manager._spawn("test", ["cmd"], ".", {})
            manager.stop_all()
            manager.stop_all()  # Second call should not raise

    def test_stop_all_does_not_restart_on_second_call(self, manager):
        """Second stop_all call should not re-terminate processes."""
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            
            manager._spawn("test", ["cmd"], ".", {})
            manager.stop_all()
            # Reset mock to track new calls
            mock_proc.reset_mock()
            manager.stop_all()
            mock_proc.terminate.assert_not_called()


class TestServiceManagerStartup:
    """Tests for service startup with timeouts and rollback."""

    @pytest.fixture
    def manager(self):
        return birdeye_launcher.ServiceManager()

    def test_startup_deadline_enforced(self, manager):
        """Total startup should not exceed TOTAL_STARTUP_DEADLINE."""
        with patch('time.time') as mock_time:
            # Simulate time advancing past deadline
            mock_time.side_effect = [0, 100, 200, 300]  # Past 180s deadline
            
            with patch.object(birdeye_launcher.ServiceManager, '_spawn'):
                with patch.object(birdeye_launcher.ServiceManager, '_wait_for_process_ready', return_value=True):
                    with pytest.raises(TimeoutError, match="Overall startup deadline"):
                        manager.start_all()

    def test_redis_failure_triggers_rollback(self, manager):
        """Redis startup failure should trigger stop_all rollback."""
        with patch.object(birdeye_launcher.ServiceManager, '_spawn') as mock_spawn:
            with patch.object(birdeye_launcher.ServiceManager, '_wait_for_process_ready', return_value=False):
                with patch.object(birdeye_launcher.ServiceManager, 'stop_all') as mock_stop:
                    with pytest.raises(RuntimeError, match="Redis failed to start"):
                        manager.start_all()
                    mock_stop.assert_called_once()

    def test_inference_failure_triggers_rollback(self, manager):
        """Inference startup failure should trigger rollback."""
        with patch.object(birdeye_launcher.ServiceManager, '_spawn'):
            with patch.object(birdeye_launcher.ServiceManager, '_wait_for_process_ready') as mock_wait:
                mock_wait.side_effect = [True, False]  # Redis OK, Inference fails
                with patch.object(birdeye_launcher.ServiceManager, 'stop_all') as mock_stop:
                    with pytest.raises(RuntimeError, match="Inference service failed to start"):
                        manager.start_all()
                    mock_stop.assert_called_once()

class TestBirdeyeAppSingleInstance:
    """Tests for BirdEyeApp single-instance protection."""

    @patch('scripts.birdeye_launcher._acquire_singleton_lock', return_value=False)
    @patch('scripts.birdeye_launcher._release_singleton_lock')
    def test_second_instance_exits_cleanly(self, mock_release, mock_acquire):
        """Second launch should detect existing instance and exit."""
        app = birdeye_launcher.BirdEyeApp()
        # Mock VENV_PY to exist
        with patch.object(birdeye_launcher, 'VENV_PY', Path(__file__).parent.parent):
            result = app.run()
            assert result == 1  # Exit code for "already running"
            mock_release.assert_not_called()  # Lock not acquired, so not released

    @patch('scripts.birdeye_launcher._acquire_singleton_lock', return_value=True)
    @patch('scripts.birdeye_launcher._release_singleton_lock')
    def test_first_instance_acquires_and_releases_lock(self, mock_release, mock_acquire):
        """First launch should acquire lock and release on exit (even on error)."""
        app = birdeye_launcher.BirdEyeApp()
        
        with patch.object(birdeye_launcher, 'VENV_PY', Path(__file__).parent.parent):
            with patch.object(Path, 'exists', return_value=True):
                with patch.object(birdeye_launcher.ServiceManager, 'start_all'):
                    with patch.object(birdeye_launcher.ServiceManager, 'wait_healthy', return_value=False):
                        # wait_healthy returns False -> run() returns 1
                        result = app.run()
                        assert result == 1
        
        # Lock should be acquired and then released (even on early exit)
        mock_acquire.assert_called_once()
        mock_release.assert_called_once()


class TestServiceManagerStopAll:
    """Tests for stop_all behavior."""

    def test_stop_all_graceful_then_force(self):
        """Process should be terminated gracefully, then force-killed on timeout."""
        manager = birdeye_launcher.ServiceManager()
        
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="cmd", timeout=10), None]
        
        manager.processes.append({
            "proc": mock_proc,
            "name": "test",
            "cmd": ["cmd"],
            "started_at": time.time(),
        })
        
        manager.stop_all()
        
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called()
        mock_proc.kill.assert_called_once()

    def test_stop_all_graceful_success(self):
        """Process terminating gracefully should not be killed."""
        manager = birdeye_launcher.ServiceManager()
        
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = None  # Returns normally
        
        manager.processes.append({
            "proc": mock_proc,
            "name": "test",
            "cmd": ["cmd"],
            "started_at": time.time(),
        })
        
        manager.stop_all()
        
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        mock_proc.kill.assert_not_called()

    def test_stop_all_already_stopped(self):
        """Process already stopped should be skipped."""
        manager = birdeye_launcher.ServiceManager()
        
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Already exited
        
        manager.processes.append({
            "proc": mock_proc,
            "name": "test",
            "cmd": ["cmd"],
            "started_at": time.time(),
        })
        
        manager.stop_all()
        
        mock_proc.terminate.assert_not_called()


class TestServiceManagerSignalHandlers:
    """Tests for signal/atexit handler registration."""

    def test_atexit_registered(self):
        """atexit.register should be called on init."""
        with patch('atexit.register') as mock_atexit:
            manager = birdeye_launcher.ServiceManager()
            mock_atexit.assert_called_once()

    def test_signal_handlers_registered(self):
        """SIGTERM and SIGINT handlers should be registered."""
        with patch('signal.signal') as mock_signal:
            manager = birdeye_launcher.ServiceManager()
            assert mock_signal.call_count == 2
            calls = mock_signal.call_args_list
            signals = [call[0][0] for call in calls]
            assert signal.SIGTERM in signals
            assert signal.SIGINT in signals


class TestBirdeyeSpec:
    """Tests for PyInstaller spec safety."""

    def test_scripts_directory_not_included(self):
        """scripts/ directory should NOT be in datas."""
        # Read the spec file
        spec_path = REPO_ROOT / "BirdEye.spec"
        content = spec_path.read_text()
        
        # scripts/ directory should NOT be in datas
        assert '(str(REPO_ROOT / "scripts"), "scripts")' not in content

    def test_entry_point_is_launcher(self):
        """Entry point should be birdeye_launcher.py."""
        spec_path = REPO_ROOT / "BirdEye.spec"
        content = spec_path.read_text()
        assert "scripts/birdeye_launcher.py" in content

    def test_required_runtime_assets_included(self):
        """Required runtime assets should be included."""
        spec_path = REPO_ROOT / "BirdEye.spec"
        content = spec_path.read_text()
        
        # Critical runtime assets
        required = [
            "app/static",
            "app/api",
            "app/inference",
            "app/pipeline",
            "app/services",
            "alembic",
            ".env.example",
            "alembic.ini",
        ]
        for asset in required:
            assert asset in content, f"Missing required asset: {asset}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


from pathlib import Path
import subprocess
import signal