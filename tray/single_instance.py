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


# ---------------------------------------------------------------------------
# "Show the window" signal: a second Polaris.exe launch asks the running one to
# open its window, then exits.  Named auto-reset event (Windows only).
# ---------------------------------------------------------------------------
SHOW_EVENT_NAME = "Local\\PolarisTrayShowWindow"
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


def _event_k32() -> Any:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.SetEvent.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def signal_show(name: str = SHOW_EVENT_NAME, kernel32: Any = None) -> bool:
    """Second instance: ask the running tray to show its window. True if delivered."""

    if kernel32 is None and os.name != "nt":
        return False
    k32 = kernel32 or _event_k32()
    handle = k32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, name)
    if not handle:
        return False
    try:
        return bool(k32.SetEvent(handle))
    finally:
        k32.CloseHandle(handle)


class ShowSignalListener:
    """First instance: run ``callback`` whenever another launch signals 'show'."""

    def __init__(self, callback: Any, name: str = SHOW_EVENT_NAME, kernel32: Any = None,
                 poll_ms: int = 500) -> None:
        self.callback = callback
        self.name = name
        self._k32 = kernel32
        self.poll_ms = poll_ms
        self._handle: Any = None
        self._stop = False
        self._thread: Any = None

    def start(self) -> bool:
        if self._k32 is None:
            if os.name != "nt":
                return False
            self._k32 = _event_k32()
        self._handle = self._k32.CreateEventW(None, False, False, self.name)  # auto-reset
        if not self._handle:
            return False
        import threading

        self._thread = threading.Thread(target=self._run, name="polaris-show-signal", daemon=True)
        self._thread.start()
        return True

    def wait_once(self) -> bool:
        """One wait slice; returns True when the event fired (callback ran)."""

        if self._k32.WaitForSingleObject(self._handle, self.poll_ms) == WAIT_OBJECT_0:
            try:
                self.callback()
            except Exception:  # never let a bad callback end the listener
                import logging

                logging.getLogger("tray").exception("pencere gösterilemedi")
            return True
        return False

    def _run(self) -> None:
        while not self._stop:
            self.wait_once()

    def stop(self) -> None:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=2 * self.poll_ms / 1000 + 0.5)
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None
