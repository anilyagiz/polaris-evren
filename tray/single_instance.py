"""One tray per Windows session: named mutex (Windows) or an exclusive lock file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MUTEX_NAME = "Local\\PolarisTraySingleInstance"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """``acquire()`` returns False when another instance already holds the guard."""

    def __init__(self, name: str = MUTEX_NAME, lock_dir: Path | None = None,
                 kernel32: Any = None, use_mutex: bool | None = None) -> None:
        self.name = name
        self.lock_dir = lock_dir
        self._kernel32 = kernel32
        self.use_mutex = (os.name == "nt") if use_mutex is None else use_mutex
        self._handle: Any = None
        self._fd: int | None = None
        self._lock_path: Path | None = None

    # -- mutex ----------------------------------------------------------------
    def _k32(self) -> Any:
        if self._kernel32 is None:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.GetLastError = lambda: ctypes.get_last_error()  # type: ignore[attr-defined]
            self._kernel32 = k32
        return self._kernel32

    def _acquire_mutex(self) -> bool:
        k32 = self._k32()
        handle = k32.CreateMutexW(None, False, self.name)
        err = k32.GetLastError()
        if not handle:
            return True  # cannot create a guard: do not block the user
        if err == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    # -- lock file ------------------------------------------------------------
    def _acquire_file(self) -> bool:
        directory = self.lock_dir or Path(os.environ.get("TEMP", "."))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "polaris-tray.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if _stale(path):
                try:
                    path.unlink()
                except OSError:
                    return False
                return self._acquire_file()
            return False
        os.write(fd, str(os.getpid()).encode())
        self._fd, self._lock_path = fd, path
        return True

    def acquire(self) -> bool:
        return self._acquire_mutex() if self.use_mutex else self._acquire_file()

    def release(self) -> None:
        if self._handle:
            self._k32().CloseHandle(self._handle)
            self._handle = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            try:
                if self._lock_path:
                    self._lock_path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "SingleInstance":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stale(path: Path) -> bool:
    try:
        pid = int(path.read_text().strip() or "0")
    except (OSError, ValueError):
        return True
    return not _pid_alive(pid)
