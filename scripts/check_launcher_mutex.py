"""Read-only probe: is Global\\BirdEyeLauncherMutex currently held?

Used by PROJECT_RELEASE_REPORT/18_REALTIME_RECOGNITION_LOGS.md to evidence why
tests/test_process_safety.py::TestSingletonMutex fails ONLY while a packaged
BirdEye.exe instance is running: those tests acquire the real Windows
single-instance mutex, so they fail whenever a live launcher owns it.

Opens the mutex with SYNCHRONIZE access (query only - it neither creates,
modifies, nor terminates anything) and reports ERROR_ALREADY_EXISTS.

    .venv\\Scripts\\python.exe scripts\\check_launcher_mutex.py
"""

from __future__ import annotations

import ctypes
import sys

MUTEX_NAME = "Global\\BirdEyeLauncherMutex"
ERROR_ALREADY_EXISTS = 183

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def main() -> int:
    open_mutex = kernel32.OpenMutexW
    open_mutex.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    open_mutex.restype = ctypes.c_void_p
    handle = open_mutex(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE: query only
    err = ctypes.get_last_error()
    if handle:
        kernel32.CloseHandle(handle)
        print(f"{MUTEX_NAME}: EXISTS (opened read-only) - held by a live launcher")
        return 0
    if err == ERROR_ALREADY_EXISTS:
        print(f"{MUTEX_NAME}: EXISTS but could not be opened for query")
        return 0
    print(f"{MUTEX_NAME}: not present (no live launcher owns it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
