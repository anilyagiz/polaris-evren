"""Polaris desktop window ("Polaris penceresi"): the side panel in a native window.

pywebview (WinForms + WebView2) shows ``http://127.0.0.1:<port>/app/`` — the
backend serves the built panel there, same origin, so no settings are needed.
The window is created hidden, toggled by the tray / Alt+Space / a second
launch, and closing it only hides it (Polaris keeps running in the tray).

Pure helpers (geometry, settings file, WebView2 detection) are kept separate
from the pywebview wrapper so they are testable without a GUI.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("tray.window")

TITLE = "Polaris"
DEFAULT_SIZE = (440, 760)
MIN_SIZE = (360, 520)
APP_PATH = "/app/"
LIGHT_BG = "#f6f2e9"  # extension/src/panel/styles.css --bg
DARK_BG = "#0b1020"
#: Evergreen WebView2 bootstrapper (Microsoft).
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_MISSING = (
    "Polaris penceresi için Microsoft Edge WebView2 Runtime gerekli, ancak bu bilgisayarda "
    "bulunamadı.\n\nİndir ve kur (ücretsiz, Microsoft):\n" + WEBVIEW2_URL + "\n\n"
    "Kurulumdan sonra Polaris'i yeniden başlat. O zamana kadar Polaris sistem tepsisinde "
    "çalışmaya devam eder; Brave'deki Polaris paneli de kullanılabilir."
)
WEBVIEW_MISSING = (
    "Polaris penceresi açılamadı: pywebview kurulu değil.\n\n"
    "Kurulum: .venv\\Scripts\\python -m pip install -r backend\\requirements-tray.txt\n\n"
    "Polaris sistem tepsisinde çalışmaya devam ediyor."
)

_WEBVIEW2_KEYS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Evergreen runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # canary
)

LOADING_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="color-scheme" content="light dark"><style>
html,body{height:100%;margin:0}body{display:grid;place-items:center;font:15px "Segoe UI",sans-serif;
background:#f6f2e9;color:#1b2440}@media (prefers-color-scheme:dark){body{background:#0b1020;color:#e8ecf7}}
.s{font-size:42px;color:#f2be3c;animation:p 1.4s ease-in-out infinite}@keyframes p{50%{opacity:.35}}
p{margin:.6em 0 0;text-align:center;opacity:.8}</style></head><body><div style="text-align:center">
<div class="s">&#10022;</div><p>Polaris başlıyor…</p><p style="font-size:12px">__DETAIL__</p></div></body></html>"""


# ---------------------------------------------------------------------------
# Geometry + settings file
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    x: int
    y: int


def centered(work: Rect, size: tuple[int, int] = DEFAULT_SIZE) -> Geometry:
    width = min(size[0], max(MIN_SIZE[0], work.width))
    height = min(size[1], max(MIN_SIZE[1], work.height - 40))
    x = work.left + max(0, (work.width - width) // 2)
    y = work.top + max(0, (work.height - height) // 2)
    return Geometry(width, height, x, y)


def initial_geometry(saved: dict[str, Any] | None, work: Rect, virtual: Rect | None = None) -> Geometry:
    """Saved position/size when it is still on a screen, else centered default."""

    if not saved:
        return centered(work)
    try:
        width = max(MIN_SIZE[0], int(saved["width"]))
        height = max(MIN_SIZE[1], int(saved["height"]))
        x, y = int(saved["x"]), int(saved["y"])
    except (KeyError, TypeError, ValueError):
        return centered(work)
    screen = virtual or work
    # At least the title bar's top-left 80x40 must be visible on the desktop.
    visible = (screen.left - width + 80 <= x <= screen.right - 80
               and screen.top <= y <= screen.bottom - 40)
    if not visible:
        return centered(work, (width, height))
    return Geometry(width, height, x, y)


class SettingsStore:
    """``backend/data/tray-settings.json`` — ``{"window": {x, y, width, height}}``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def window(self) -> dict[str, Any] | None:
        value = self.read().get("window")
        return value if isinstance(value, dict) else None

    def save_window(self, geometry: Geometry) -> None:
        with self._lock:
            data = self.read()
            data["window"] = {"x": geometry.x, "y": geometry.y,
                              "width": geometry.width, "height": geometry.height}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                log.warning("pencere ayarları kaydedilemedi: %s", self.path)


# ---------------------------------------------------------------------------
# Windows queries (ctypes / winreg; harmless defaults elsewhere)
# ---------------------------------------------------------------------------
def work_area() -> Rect:
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return Rect(rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return Rect(0, 0, 1366, 728)


def virtual_screen() -> Rect | None:
    try:
        import ctypes

        metric = ctypes.windll.user32.GetSystemMetrics
        left, top = metric(76), metric(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        return Rect(left, top, left + metric(78), top + metric(79))
    except Exception:
        return None


def webview2_installed(reader: Callable[[str, str], str | None] | None = None) -> bool:
    """WebView2 runtime registered (per-machine or per-user) with a real version."""

    read = reader or _read_pv
    for key in _WEBVIEW2_KEYS:
        for path in (rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}",
                     rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}"):
            for hive in ("HKLM", "HKCU"):
                version = read(hive, path)
                if version and version.strip() not in ("", "0.0.0.0"):
                    return True
    return False


def _read_pv(hive: str, path: str) -> str | None:
    try:
        import winreg

        root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, "pv")
            return str(value)
    except Exception:
        return None


def prefers_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except Exception:
        return False


def app_url(base_url: str) -> str:
    return base_url.rstrip("/") + APP_PATH


# ---------------------------------------------------------------------------
# pywebview wrapper
# ---------------------------------------------------------------------------
class DesktopWindow:
    """One hidden-at-start pywebview window; ``run()`` owns the main thread."""

    def __init__(self, store: SettingsStore, storage_path: Path, icon_path: Path | None = None,
                 webview_module: Any = None, debug_port: int | None = None) -> None:
        self.store = store
        self.storage_path = storage_path
        self.icon_path = icon_path
        self._webview = webview_module
        self.debug_port = debug_port
        self.window: Any = None
        self.visible = False
        self.quitting = False
        self.started = threading.Event()
        self._url: str | None = None
        self._loaded_url: str | None = None
        self._want_show = False
        self._save_timer: threading.Timer | None = None
        self._permissions_hooked = False
        self._lock = threading.RLock()

    # -- creation ---------------------------------------------------------
    def create(self) -> Any:
        webview = self._wv()
        if self.debug_port:
            webview.settings["REMOTE_DEBUGGING_PORT"] = int(self.debug_port)
        geo = initial_geometry(self.store.window(), work_area(), virtual_screen())
        self.window = webview.create_window(
            TITLE,
            html=LOADING_HTML.replace("__DETAIL__", "Sunucu hazırlanıyor"),
            width=geo.width,
            height=geo.height,
            x=geo.x,
            y=geo.y,
            min_size=MIN_SIZE,
            hidden=True,
            background_color=DARK_BG if prefers_dark() else LIGHT_BG,
            text_select=True,
        )
        events = self.window.events
        events.closing += self._on_closing
        events.moved += self._schedule_save
        events.resized += self._schedule_save
        events.loaded += self._on_loaded
        return self.window

    def _wv(self) -> Any:
        if self._webview is None:
            import webview

            self._webview = webview
        return self._webview

    def run(self, on_started: Callable[[], None] | None = None) -> None:
        """Blocks on the GUI loop (main thread) until ``destroy()``."""

        webview = self._wv()
        self.storage_path.mkdir(parents=True, exist_ok=True)

        def started() -> None:
            self.started.set()
            if on_started:
                on_started()

        kwargs: dict[str, Any] = {"private_mode": False, "storage_path": str(self.storage_path)}
        if self.icon_path and self.icon_path.is_file():
            kwargs["icon"] = str(self.icon_path)
        webview.start(started, gui="edgechromium", **kwargs)

    # -- url --------------------------------------------------------------
    def set_backend(self, base_url: str | None) -> None:
        """Called when the backend is (or stops being) healthy at ``base_url``."""

        with self._lock:
            self._url = app_url(base_url) if base_url else None
            if self._url and self._url != self._loaded_url and self.window is not None and self.started.is_set():
                self._loaded_url = self._url
                log.info("pencere yükleniyor: %s", self._url)
                self.window.load_url(self._url)

    # -- show / hide --------------------------------------------------------
    def show(self) -> None:
        with self._lock:
            if self.window is None or self.quitting:
                return
            if not self.started.is_set():
                self._want_show = True
                return
            self.set_backend(self._url[: -len(APP_PATH)] if self._url else None)
            self.window.show()
            try:
                self.window.restore()
            except Exception:
                pass
            self.visible = True
            _bring_to_front(TITLE)

    def hide(self) -> None:
        with self._lock:
            if self.window is None:
                return
            self._save_now()
            self.window.hide()
            self.visible = False

    def toggle(self) -> None:
        if self.visible and _is_foreground(TITLE):
            self.hide()
        else:
            self.show()

    def flush_pending_show(self) -> None:
        if self._want_show:
            self._want_show = False
            self.show()

    def destroy(self) -> None:
        with self._lock:
            self.quitting = True
            if self.window is None:
                return
            self._save_now()
            try:
                self.window.destroy()
            except Exception:
                log.exception("pencere kapatılamadı")

    # -- events -----------------------------------------------------------
    def _on_closing(self) -> bool:
        if self.quitting:
            return True
        # X button: only hide; Polaris keeps running in the tray.
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def _geometry(self) -> Geometry | None:
        w = self.window
        try:
            if w is None or w.width < MIN_SIZE[0] // 2:
                return None
            return Geometry(int(w.width), int(w.height), int(w.x), int(w.y))
        except Exception:
            return None

    def _save_now(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        geometry = self._geometry()
        if geometry is not None and self.visible:
            self.store.save_window(geometry)

    def _schedule_save(self, *_args: Any) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.8, self._save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _on_loaded(self, *_args: Any) -> None:
        if not self._permissions_hooked:
            self._permissions_hooked = grant_microphone(self.window, self._url or "")


def is_own_app_uri(uri: str) -> bool:
    """Only the backend's own /app/ page on loopback gets the microphone."""

    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(uri))
    except ValueError:
        return False
    return (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
            and parts.path.startswith(APP_PATH))


def grant_microphone(window: Any, _url: str = "") -> bool:
    """Auto-allow the microphone for our own /app/ page (WebView2 would prompt)."""

    try:
        from Microsoft.Web.WebView2.Core import (  # type: ignore[import-not-found]
            CoreWebView2PermissionKind,
            CoreWebView2PermissionState,
        )
        from System import Func, Type  # type: ignore[import-not-found]

        form = window.native

        def on_permission(_sender: Any, args: Any) -> None:
            if args.PermissionKind == CoreWebView2PermissionKind.Microphone and is_own_app_uri(args.Uri):
                args.State = CoreWebView2PermissionState.Allow

        def attach() -> None:  # CoreWebView2 is only reachable on the UI thread
            form.browser.webview.CoreWebView2.PermissionRequested += on_permission

        form.Invoke(Func[Type](attach))
        log.info("mikrofon izni /app/ sayfası için otomatik verilecek")
        return True
    except Exception as exc:
        log.info("mikrofon izni otomatikleştirilemedi (WebView2 soracak): %s", exc)
        return False


def _find_window(title: str) -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.FindWindowW(None, title) or 0)
    except Exception:
        return 0


def _is_foreground(title: str) -> bool:
    try:
        import ctypes

        hwnd = _find_window(title)
        return bool(hwnd) and int(ctypes.windll.user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return True


def _bring_to_front(title: str) -> None:
    """Hotkey/tray threads may not own the foreground: nudge Windows explicitly."""

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _find_window(title)
        if not hwnd:
            return
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # A synthetic Alt tap lifts the foreground lock (documented workaround).
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
