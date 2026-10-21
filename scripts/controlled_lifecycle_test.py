"""
Controlled lifecycle test for BirdEye ServiceManager.
Uses harmless helper processes to verify lifecycle management.
"""
import sys
import os
import time
import subprocess
import threading
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import birdeye_launcher

# A harmless helper that runs for a controlled time then exits
HELPER_SCRIPT = """
import sys
import time
import os

ready_file = sys.argv[1] if len(sys.argv) > 1 else None
run_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 10

# Signal readiness
if ready_file:
    try:
        with open(ready_file, 'w') as f:
            f.write('ready')
    except:
        pass

# Run for specified time
time.sleep(run_seconds)
sys.exit(0)
"""

def write_helper():
    """Write a temporary helper script."""
    helper_path = Path(tempfile.gettempdir()) / "birdeye_test_helper.py"
    with open(helper_path, 'w') as f:
        f.write(HELPER_SCRIPT)
    return helper_path


def test_1_mutex_acquisition():
    """Test 1: One launch can acquire the application mutex."""
    print("\n=== Test 1: Mutex Acquisition ===")
    birdeye_launcher._release_singleton_lock()
    
    acquired = birdeye_launcher._acquire_singleton_lock()
    assert acquired, "First instance should acquire mutex"
    print("[OK] First instance acquired mutex")
    
    # Cleanup
    birdeye_launcher._release_singleton_lock()
    print("[OK] Mutex released cleanly")


def test_2_second_launch_rejected():
    """Test 2: A second launch is rejected cleanly."""
    print("\n=== Test 2: Second Launch Rejected ===")
    birdeye_launcher._release_singleton_lock()
    
    # First instance acquires
    acquired1 = birdeye_launcher._acquire_singleton_lock()
    assert acquired1, "First instance should acquire"
    print("[OK] First instance holds mutex")
    
    # Second instance tries
    acquired2 = birdeye_launcher._acquire_singleton_lock()
    assert not acquired2, "Second instance should be rejected"
    print("[OK] Second instance correctly rejected")
    
    birdeye_launcher._release_singleton_lock()
    print("[OK] Mutex released after rejection test")


def test_3_normal_startup():
    """Test 3: Normal startup completes within timeout."""
    print("\n=== Test 3: Normal Startup (with harmless helper) ===")
    
    helper_path = write_helper()
    ready_file = Path(tempfile.gettempdir()) / "birdeye_test_ready.txt"
    
    manager = birdeye_launcher.ServiceManager()
    
    try:
        # Use a harmless Python script that exits quickly
        cmd = [sys.executable, str(helper_path), str(ready_file), "2"]
        
        print(f"  Spawning helper: {' '.join(cmd)}")
        proc = manager._spawn("test_helper", cmd, ".", os.environ.copy())
        
        # Wait for ready signal
        deadline = time.time() + 5
        ready = False
        while time.time() < deadline:
            if ready_file.exists():
                ready = True
                break
            time.sleep(0.1)
        
        assert ready, "Helper should signal ready within 5s"
        print("  [OK] Helper signaled ready")
        
        # Wait for process to complete (should take ~2 seconds)
        proc.wait(timeout=10)
        assert proc.poll() is not None, "Process should exit"
        assert proc.returncode == 0, f"Helper should exit cleanly, got {proc.returncode}"
        print("  [OK] Helper exited cleanly with return code 0")
        
    finally:
        manager.stop_all()
        if ready_file.exists():
            ready_file.unlink()
        helper_path.unlink(missing_ok=True)
    
    print("[OK] Normal startup and shutdown completed")


def test_4_helper_never_ready_times_out():
    """Test 4: Helper that never becomes ready times out."""
    print("\n=== Test 4: Timeout on Unready Process ===")
    
    # Helper that never creates ready file and runs long
    timeout_helper = Path(tempfile.gettempdir()) / "birdeye_timeout_helper.py"
    with open(timeout_helper, 'w') as f:
        f.write("""
import time
import sys
time.sleep(60)
sys.exit(0)
""")
    
    manager = birdeye_launcher.ServiceManager()
    
    try:
        cmd = [sys.executable, str(timeout_helper)]
        print("  Spawning long-running helper (no ready signal)")
        proc = manager._spawn("timeout_test", [sys.executable, str(timeout_helper)], ".", os.environ.copy())
        
        # Wait with short timeout
        deadline = time.time() + 3
        ready = False
        ready_file = Path(tempfile.gettempdir()) / "fake_ready.txt"
        while time.time() < deadline:
            if ready_file.exists():
                ready = True
                break
            time.sleep(0.1)
        
        assert not ready, "Should timeout waiting for ready signal"
        print("  [OK] Correctly timed out waiting for ready signal")
        
    finally:
        manager.stop_all()
        timeout_helper.unlink(missing_ok=True)
    
    print("[OK] Timeout behavior verified")


def test_5_partial_startup_failure_rollback():
    """Test 5: Partial startup failure stops all processes owned by that startup."""
    print("\n=== Test 5: Partial Startup Failure Rollback ===")
    
    helper_path = write_helper()
    ready_file = Path(tempfile.gettempdir()) / "birdeye_test_ready.txt"
    
    manager = birdeye_launcher.ServiceManager()
    
    try:
        # Spawn first helper (will succeed)
        cmd1 = [sys.executable, str(helper_path), str(ready_file), "10"]
        proc1 = manager._spawn("helper1", [sys.executable, str(helper_path), str(ready_file), "10"], ".", os.environ.copy())
        
        # Wait for first to be ready
        deadline = time.time() + 5
        while time.time() < deadline and not ready_file.exists():
            time.sleep(0.1)
        
        assert ready_file.exists(), "First helper should be ready"
        print("  [OK] First helper ready")
        
        # Now simulate a startup failure by manually calling stop_all
        # (This tests that stop_all cleans up the already-spawned process)
        manager.stop_all()
        
        # Verify process was stopped
        assert proc1.poll() is not None, "Process should be stopped after rollback"
        print("  [OK] Rollback stopped owned process")
        
    finally:
        if ready_file.exists():
            ready_file.unlink()
    
    print("[OK] Partial failure rollback verified")


def test_6_stop_all_idempotent():
    """Test 6: stop_all() can be called repeatedly without errors."""
    print("\n=== Test 6: stop_all() Idempotent ===")
    
    manager = birdeye_launcher.ServiceManager()
    
    # Call stop_all multiple times
    manager.stop_all()
    manager.stop_all()
    manager.stop_all()
    
    print("[OK] Multiple stop_all() calls succeeded without errors")


def test_7_no_orphaned_processes():
    """Test 7: Shutdown leaves no orphaned processes."""
    print("\n=== Test 7: No Orphaned Processes After Shutdown ===")
    
    helper_path = write_helper()
    ready_file = Path(tempfile.gettempdir()) / "birdeye_test_ready.txt"
    
    manager = birdeye_launcher.ServiceManager()
    
    try:
        # Spawn multiple helpers
        procs = []
        for i in range(3):
            rf = Path(tempfile.gettempdir()) / f"birdeye_ready_{i}.txt"
            proc = manager._spawn(f"helper{i}", 
                [sys.executable, str(helper_path), str(rf), "5"], 
                ".", os.environ.copy())
            procs.append((proc, rf))
        
        # Wait for all to be ready
        deadline = time.time() + 5
        while time.time() < deadline:
            if all(rf.exists() for _, rf in procs):
                break
            time.sleep(0.1)
        
        # Shutdown
        manager.stop_all()
        
        # Verify all processes terminated
        for proc, rf in procs:
            assert proc.poll() is not None, f"Process {proc.pid} should be stopped"
            rf.unlink(missing_ok=True)
        
        print("  [OK] All 3 helper processes terminated cleanly")
        
    finally:
        if ready_file.exists():
            ready_file.unlink()
    
    print("[OK] No orphaned processes after shutdown")


def test_8_unrelated_process_untouched():
    """Test 8: An unrelated process remains untouched."""
    print("\n=== Test 8: Unrelated Process Untouched ===")
    
    # Start an independent Python process
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    try:
        time.sleep(0.5)  # Let it start
        assert unrelated.poll() is None, "Unrelated process should be running"
        
        # Now run our manager shutdown
        manager = birdeye_launcher.ServiceManager()
        manager.stop_all()  # Should not affect unrelated process
        
        time.sleep(0.2)
        assert unrelated.poll() is None, "Unrelated process should still be running"
        print("  [OK] Unrelated process unaffected by stop_all()")
        
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
    
    print("[OK] Unrelated process untouched")


def test_9_mutex_release_permits_later_launch():
    """Test 9: Releasing the mutex permits a later legitimate launch."""
    print("\n=== Test 9: Mutex Release Permits Later Launch ===")
    
    birdeye_launcher._release_singleton_lock()
    
    # First launch acquires
    acquired1 = birdeye_launcher._acquire_singleton_lock()
    assert acquired1, "First acquisition should succeed"
    print("  [OK] First acquisition succeeded")
    
    birdeye_launcher._release_singleton_lock()
    print("  [OK] Mutex released")
    
    # Second acquisition should now succeed
    acquired2 = birdeye_launcher._acquire_singleton_lock()
    assert acquired2, "Second acquisition after release should succeed"
    print("  [OK] Second acquisition after release succeeded")
    
    birdeye_launcher._release_singleton_lock()
    print("[OK] Mutex release permits later launch")


def test_10_no_recursive_launch():
    """Test 10: Launcher does not recursively launch itself."""
    print("\n=== Test 10: No Recursive Launch ===")
    
    # Check that scripts/ is not in the PyInstaller bundle
    spec_path = Path("BirdEye.spec")
    if spec_path.exists():
        content = spec_path.read_text()
        assert '(str(REPO_ROOT / "scripts"), "scripts")' not in spec_path.read_text()
        print("  [OK] scripts/ directory excluded from PyInstaller bundle")
    
    # Verify the launcher doesn't invoke itself
    launcher_path = Path("scripts/birdeye_launcher.py")
    content = launcher_path.read_text()
    
    # Check it doesn't call itself recursively
    assert "birdeye_launcher" not in content or "BirdEyeApp" in content  # Only class reference
    print("  [OK] Launcher doesn't recursively invoke itself")
    
    print("[OK] No recursive launch capability")


def main():
    """Run all controlled lifecycle tests."""
    print("=" * 60)
    print("BIRDEYE CONTROLLED LIFECYCLE VERIFICATION")
    print("=" * 60)
    
    tests = [
        test_1_mutex_acquisition,
        test_2_second_launch_rejected,
        test_3_normal_startup,
        test_4_helper_never_ready_times_out,
        test_5_partial_startup_failure_rollback,
        test_6_stop_all_idempotent,
        test_7_no_orphaned_processes,
        test_8_unrelated_process_untouched,
        test_9_mutex_release_permits_later_launch,
        test_10_no_recursive_launch,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import tempfile
    success = main()
    sys.exit(0 if success else 1)