"""Global hotkey (Alt+Space, else Ctrl+Alt+Space) via Win32 RegisterHotKey.

No extra dependency: ctypes only.  The hotkey is registered on a background
thread with ``hWnd=NULL``, so ``WM_HOTKEY`` arrives in that thread's message
queue; ``stop()`` posts ``WM_QUIT`` to end the loop.  ``user32``/``kernel32`` are
injectable so the logic is testable without Windows.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("tray.hotkey")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 0x5A11


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    vk: int
    label: str


#: Tried in order; the first one Windows accepts wins.
CANDIDATES: tuple[Hotkey, ...] = (
    Hotkey(MOD_ALT, VK_SPACE, "Alt+Space"),
    Hotkey(MOD_CONTROL | MOD_ALT, VK_SPACE, "Ctrl+Alt+Space"),
)


def _default_user32() -> Any:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    return user32


def _default_thread_id() -> int:  # pragma: no cover - Windows only
    import ctypes

    return int(ctypes.windll.kernel32.GetCurrentThreadId())


def register_first(user32: Any, candidates: tuple[Hotkey, ...] = CANDIDATES,
                   hotkey_id: int = HOTKEY_ID) -> Hotkey | None:
    """Register the first available candidate (on the calling thread)."""

    for index, key in enumerate(candidates):
        if user32.RegisterHotKey(None, hotkey_id, key.modifiers | MOD_NOREPEAT, key.vk):
            if index:
                log.warning("%s başka bir uygulamada kayıtlı; kısayol %s olarak ayarlandı",
                            candidates[0].label, key.label)
            return key
        log.info("kısayol kaydedilemedi: %s", key.label)
    log.warning("hiçbir genel kısayol kaydedilemedi (%s)", ", ".join(k.label for k in candidates))
    return None


class HotkeyListener:
    """Background thread: calls ``callback`` on every press of the hotkey."""

    def __init__(self, callback: Callable[[], None], user32: Any = None,
                 thread_id: Callable[[], int] | None = None,
                 candidates: tuple[Hotkey, ...] = CANDIDATES) -> None:
        self.callback = callback
        self._user32 = user32
        self._thread_id_fn = thread_id or _default_thread_id
        self.candidates = candidates
        self.hotkey: Hotkey | None = None
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._ready = threading.Event()

    @property
    def label(self) -> str:
        return self.hotkey.label if self.hotkey else ""

    def start(self, timeout: float = 2.0) -> Hotkey | None:
        """Start the thread; returns the registered hotkey (or None) once known."""

        self._thread = threading.Thread(target=self._run, name="polaris-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.hotkey

    def _run(self) -> None:
        try:
            user32 = self._user32 = self._user32 or _default_user32()
            self._tid = self._thread_id_fn()
            self.hotkey = register_first(user32, self.candidates)
        except Exception:
            log.exception("kısayol başlatılamadı")
            self._ready.set()
            return
        self._ready.set()
        if self.hotkey is None:
            return
        log.info("genel kısayol: %s", self.hotkey.label)
        try:
            self.loop(user32)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def loop(self, user32: Any) -> None:
        """GetMessage loop until WM_QUIT (0 / -1 return)."""

        import ctypes
        from ctypes import wintypes

        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                return
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.fire()

    def fire(self) -> None:
        try:
            self.callback()
        except Exception:
            log.exception("kısayol eylemi başarısız")

    def stop(self) -> None:
        if self._tid is not None and self._user32 is not None:
            try:
                self._user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:  # pragma: no cover - defensive
                pass
