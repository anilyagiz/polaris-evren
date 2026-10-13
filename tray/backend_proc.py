"""Start/stop the backend as a hidden child process."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Callable

from .paths import Paths
from .settings import BackendAddress

log = logging.getLogger("tray.backend")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
LOG_ROTATE_BYTES = 5 * 1024 * 1024
STOP_GRACE_S = 5.0


def python_for_backend(paths: Paths, windowless: bool = True) -> Path:
    """The venv interpreter; pythonw.exe so no console window ever appears."""

    preferred = paths.pythonw if windowless else paths.python
    if preferred.exists():
        return preferred
    if paths.python.exists():
        return paths.python
    # Last resort (dev without .venv): the interpreter running the tray, unless frozen.
    if not getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if windowless and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
            return exe.parent / "pythonw.exe"
        return exe
    return preferred  # will fail loudly in start()


def build_command(python: Path, address: BackendAddress, use_backend_main: bool) -> list[str]:
    """`python -m backend --host H --port P`, or the uvicorn fallback."""

    if use_backend_main:
        return [str(python), "-m", "backend", "--host", address.host, "--port", str(address.port)]
    return [str(python), "-m", "uvicorn", "backend.main:app",
            "--host", address.host, "--port", str(address.port)]


def build_env(address: BackendAddress, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["HOST"] = address.host
    env["PORT"] = str(address.port)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["POLARIS_LAUNCHER"] = "tray"
    return env


def creation_flags() -> int:
    return (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0


def rotate_log(path: Path, limit: int = LOG_ROTATE_BYTES) -> None:
    try:
        if path.exists() and path.stat().st_size > limit:
            backup = path.with_suffix(path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except OSError:
        pass


class KillOnCloseJob:
    """Windows Job Object: the backend dies with the tray even if the tray is killed."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    def assign(self, proc: subprocess.Popen) -> None:
        handle = getattr(proc, "_handle", None)
        if handle is not None and not self._k32.AssignProcessToJobObject(self.handle, int(handle)):
            import ctypes

            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")


class BackendProcess:
    """Owns at most one backend child process. Thread-safe."""

    def __init__(self, paths: Paths, address: BackendAddress,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 use_job: bool | None = None) -> None:
        self.paths = paths
        self.address = address
        self._popen = popen
        self._job: KillOnCloseJob | None = None
        if use_job if use_job is not None else (os.name == "nt" and popen is subprocess.Popen):
            try:
                self._job = KillOnCloseJob()
            except Exception as exc:  # pragma: no cover - very old Windows / sandbox
                log.warning("job object kurulamadı: %s", exc)
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None
        self._lock = threading.RLock()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def command(self) -> list[str]:
        return build_command(python_for_backend(self.paths), self.address, self.paths.has_backend_main)

    def is_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def exit_code(self) -> int | None:
        with self._lock:
            return None if self._proc is None else self._proc.poll()

    def start(self) -> None:
        with self._lock:
            if self.is_alive():
                return
            self._close_log()
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            rotate_log(self.paths.backend_log)
            self._log = open(self.paths.backend_log, "ab", buffering=0)  # noqa: SIM115
            cmd = self.command()
            log.info("backend başlatılıyor: %s (cwd=%s)", cmd, self.paths.root)
            self._log.write(f"\n===== tray: {' '.join(cmd)} =====\n".encode("utf-8"))
            self._proc = self._popen(
                cmd,
                cwd=str(self.paths.root),
                env=build_env(self.address),
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags(),
                close_fds=True,
            )
            if self._job is not None:
                try:
                    self._job.assign(self._proc)
                except OSError as exc:  # pragma: no cover
                    log.warning("backend job object'e eklenemedi: %s", exc)

    def stop(self, grace_s: float = STOP_GRACE_S) -> None:
        """terminate(), then kill() if it is still alive after ``grace_s``."""

        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                log.info("backend durduruluyor (pid %s)", proc.pid)
                try:
                    proc.terminate()
                    proc.wait(timeout=grace_s)
                except subprocess.TimeoutExpired:
                    log.warning("backend %ss içinde kapanmadı, kill", grace_s)
                    proc.kill()
                    try:
                        proc.wait(timeout=grace_s)
                    except subprocess.TimeoutExpired:
                        pass
                except OSError as exc:
                    log.warning("backend durdurulamadı: %s", exc)
            self._proc = None
            self._close_log()

    def restart(self) -> None:
        with self._lock:
            self.stop()
            self.start()

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
