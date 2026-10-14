"""Polaris desktop mascot: the PolarisPet star in a tiny always-on-top window.

A second pywebview window (next to the main "Polaris penceresi") that loads
``/app/src/panel/mascot.html`` from the backend.  It is frameless, has no
taskbar button, stays on top and can be dragged anywhere; its position and the
"Maskotu göster/gizle" choice live in ``backend/data/tray-settings.json`` under
``"mascot"``.

Transparency (verified with pywebview 6.2 / WebView2 on Windows 10): pywebview's
``transparent=True`` leaves a grey form behind the page, and a WebView2 painted
in the key colour stays a black box (its GPU output is not exactly the key).
What works: WebView2 ``DefaultBackgroundColor = Transparent`` + the WinForms form
painted in the key colour (#010203) + ``TransparencyKey`` (plus Win32
``SetLayeredWindowAttributes(LWA_COLORKEY)``), applied after the window is shown.
The page background is transparent, so only the star and its bubble remain.

Clicks: a colour-keyed window lets the mouse fall through every key-coloured
pixel of its GDI surface, and WebView2 draws through DirectComposition (not into
that surface), so the whole window would be click-through.  The page therefore
reports "hit regions" (a circle inside the star, the bubble's rectangle) via
``set_hit_regions``; they are painted, in an opaque colour, into the form's
``BackgroundImage`` *underneath* opaque page content: invisible, but clickable.

Pure helpers (settings, geometry) are testable without a GUI; the window
wrapper takes an injectable ``webview`` module.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("tray.mascot")

TITLE = "Polaris maskot"
MASCOT_PATH = "/app/src/panel/mascot.html"
#: Logical pixels (pywebview converts to physical with the monitor's DPI).
COLLAPSED = (150, 150)
EXPANDED = (360, 420)
#: Gap to the work area's bottom-right corner by default.
MARGIN = (12, 4)
KEY_COLOR = "#010203"
#: Part of the collapsed window (bottom-right, where the star sits) that must stay on screen.
VISIBLE_PART = 100
SETTINGS_KEY = "mascot"
#: At most this many hit shapes from the page (star + bubble need two).
MAX_HIT_REGIONS = 8
HIT_KINDS = ("circle", "rect")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class MascotSettings:
    """``tray-settings.json`` → ``{"mascot": {"enabled": bool, "right": int, "bottom": int}}``.

    ``right``/``bottom`` are the window's bottom-right corner in logical pixels:
    the corner the star sits in, so collapsing/expanding never moves it.
    Other keys of the file (e.g. the main window's ``"window"``) are preserved.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read_all(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _section(self) -> dict[str, Any]:
        value = self._read_all().get(SETTINGS_KEY)
        return value if isinstance(value, dict) else {}

    @property
    def enabled(self) -> bool:
        value = self._section().get("enabled", True)
        return value if isinstance(value, bool) else True

    def anchor(self) -> tuple[int, int] | None:
        section = self._section()
        try:
            return int(section["right"]), int(section["bottom"])
        except (KeyError, TypeError, ValueError):
            return None

    def _update(self, **values: Any) -> None:
        with self._lock:
            data = self._read_all()
            section = data.get(SETTINGS_KEY)
            section = dict(section) if isinstance(section, dict) else {}
            section.update(values)
            data[SETTINGS_KEY] = section
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".mascot.tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                log.warning("maskot ayarları kaydedilemedi: %s", self.path)

    def set_enabled(self, enabled: bool) -> None:
        self._update(enabled=bool(enabled))

    def save_anchor(self, right: int, bottom: int) -> None:
        self._update(right=int(right), bottom=int(bottom))


# ---------------------------------------------------------------------------
# Geometry (logical pixels)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class Geometry:
    x: int
    y: int
    width: int
    height: int

    @property
    def anchor(self) -> tuple[int, int]:
        return self.x + self.width, self.y + self.height


def size_for(expanded: bool) -> tuple[int, int]:
    return EXPANDED if expanded else COLLAPSED


def default_anchor(work: Box) -> tuple[int, int]:
    """Bottom-right of the primary work area (above the taskbar)."""

    return work.right - MARGIN[0], work.bottom - MARGIN[1]


def anchor_on_screen(anchor: tuple[int, int], screen: Box) -> bool:
    """The star (bottom-right VISIBLE_PART square of the window) is on the desktop."""

    right, bottom = anchor
    return (screen.left + VISIBLE_PART <= right <= screen.right
            and screen.top + VISIBLE_PART <= bottom <= screen.bottom)


def geometry_for(anchor: tuple[int, int] | None, expanded: bool, work: Box,
                 screen: Box | None = None) -> Geometry:
    """Window rectangle with its bottom-right corner at ``anchor``.

    A missing or off-screen anchor falls back to the work area's corner; the
    expanded window is pushed back onto the screen when it would stick out.
    """

    bounds = screen or work
    if anchor is None or not anchor_on_screen(anchor, bounds):
        anchor = default_anchor(work)
    width, height = size_for(expanded)
    x, y = anchor[0] - width, anchor[1] - height
    x = max(bounds.left, min(x, bounds.right - width))
    y = max(bounds.top, min(y, bounds.bottom - height))
    return Geometry(x, y, width, height)


def _system_scale() -> float:
    try:
        import ctypes

        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        return dpi / 96 if dpi > 0 else 1.0
    except Exception:
        return 1.0


def logical_work_area() -> Box:
    from .desktop_window import work_area

    rect, scale = work_area(), _system_scale()
    return Box(int(rect.left / scale), int(rect.top / scale),
               int(rect.right / scale), int(rect.bottom / scale))


def logical_virtual_screen() -> Box | None:
    from .desktop_window import virtual_screen

    rect = virtual_screen()
    if rect is None or rect.right <= rect.left:
        return None
    scale = _system_scale()
    return Box(int(rect.left / scale), int(rect.top / scale),
               int(rect.right / scale), int(rect.bottom / scale))


@dataclass(frozen=True)
class HitShape:
    kind: str  # "circle" | "rect"
    x: int
    y: int
    width: int
    height: int


def hit_shapes(regions: Any, scale: float, limit: tuple[int, int] | None = None) -> list[HitShape]:
    """Validate the page's hit regions (CSS px) and convert them to client pixels."""

    shapes: list[HitShape] = []
    if not isinstance(regions, list):
        return shapes
    for item in regions[:MAX_HIT_REGIONS]:
        if not isinstance(item, dict) or item.get("kind") not in HIT_KINDS:
            continue
        try:
            x, y, w, h = (float(item[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(v == v and abs(v) < 100_000 for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue  # NaN / absurd values
        px, py = int(round(x * scale)), int(round(y * scale))
        pw, ph = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        if limit is not None:
            px, py = max(0, px), max(0, py)
            pw, ph = min(pw, limit[0] - px), min(ph, limit[1] - py)
            if pw <= 0 or ph <= 0:
                continue
        shapes.append(HitShape(item["kind"], px, py, pw, ph))
    return shapes


def mascot_url(base_url: str) -> str:
    return base_url.rstrip("/") + MASCOT_PATH


# ---------------------------------------------------------------------------
# pywebview
# ---------------------------------------------------------------------------
class MascotApi:
    """``window.pywebview.api`` of the mascot page.

    Only these three methods are public: pywebview exposes every public
    attribute to JavaScript, so the host is kept in a private slot.
    """

    def __init__(self, host: "MascotWindow") -> None:
        self._host = host

    def open_main(self) -> bool:
        return self._host.open_main()

    def set_expanded(self, expanded: Any) -> bool:
        return self._host.set_expanded(bool(expanded))

    def hide_mascot(self) -> bool:
        # "Şimdilik gizle": session only. The persistent switch is the tray menu,
        # so an accidental click never makes the mascot disappear for good.
        self._host.hide()
        return True

    def set_hit_regions(self, regions: Any) -> bool:
        return self._host.set_hit_regions(regions)


class MascotWindow:
    def __init__(self, settings: MascotSettings, on_open_main: Callable[[], Any] | None = None,
                 webview_module: Any = None,
                 on_change: Callable[[], None] | None = None,
                 work: Callable[[], Box] = logical_work_area,
                 screen: Callable[[], Box | None] = logical_virtual_screen) -> None:
        self.settings = settings
        self._on_open_main = on_open_main
        self._on_change = on_change
        self._webview = webview_module
        self._work = work
        self._screen = screen
        self.window: Any = None
        self.expanded = False
        self.visible = False
        self.quitting = False
        self._url: str | None = None
        self._loaded_url: str | None = None
        self._styled = False
        self._mic_hooked = False
        self._hit_regions: list[Any] = []
        self._save_timer: threading.Timer | None = None
        self._lock = threading.RLock()
        #: Set once ``webview.start`` runs (``mark_started``); pywebview calls wait for it.
        self.started = threading.Event()

    # -- creation -----------------------------------------------------------
    def _wv(self) -> Any:
        if self._webview is None:
            import webview

            self._webview = webview
        return self._webview

    def geometry(self, expanded: bool | None = None) -> Geometry:
        return geometry_for(self.settings.anchor(), self.expanded if expanded is None else expanded,
                            self._work(), self._screen())

    def create(self, base_url: str | None = None) -> Any:
        """Create the (hidden) window; works before and after ``webview.start``."""

        geo = self.geometry(False)
        self._url = mascot_url(base_url) if base_url else None
        kwargs: dict[str, Any] = dict(
            width=geo.width, height=geo.height, x=geo.x, y=geo.y,
            resizable=False, min_size=(80, 80), frameless=True, easy_drag=False, shadow=False,
            on_top=True, hidden=True, focus=True, text_select=True,
            background_color=KEY_COLOR, js_api=MascotApi(self),
        )
        if self._url:
            kwargs["url"] = self._url
            self._loaded_url = self._url
        else:
            kwargs["html"] = "<html><body style='background:transparent'></body></html>"
        self.window = self._wv().create_window(TITLE, **kwargs)
        events = self.window.events
        events.closing += self._on_closing
        events.moved += self._schedule_save
        events.resized += self._on_resized
        events.loaded += self._on_loaded
        return self.window

    # -- backend url --------------------------------------------------------
    def set_backend(self, base_url: str | None) -> None:
        """Called once the backend is healthy; loads the page and shows it if enabled."""

        with self._lock:
            if not base_url or self.window is None or self.quitting:
                return
            self._url = mascot_url(base_url)
            if not self.started.is_set():
                return  # mark_started() finishes the job
            if self._url != self._loaded_url:
                self._loaded_url = self._url
                log.info("maskot yükleniyor: %s", self._url)
                self.window.load_url(self._url)
            if self.settings.enabled and not self.visible:
                self.show()

    def mark_started(self) -> None:
        """The GUI loop is running: apply a backend URL that arrived earlier."""

        self.started.set()
        if self._url:
            self.set_backend(self._url[: -len(MASCOT_PATH)])

    # -- visibility ---------------------------------------------------------
    def show(self) -> None:
        with self._lock:
            if self.window is None or self.quitting or not self._url or not self.started.is_set():
                return
            self.window.show()
            self.visible = True
            # pywebview sizes the form before dropping its border, so the frameless
            # window ends up smaller than asked (e.g. 136x112 for 150x150): fix it.
            try:
                self.window.resize(*size_for(self.expanded))
            except Exception:
                log.info("maskot boyutu düzeltilemedi")
            # After show(): a colour key set on a hidden form did not stick (black box).
            self._apply_style()
            self._paint_hit_mask()
            self._start_topmost_keeper()

    def _start_topmost_keeper(self) -> None:
        """Re-assert HWND_TOPMOST every few seconds while visible.

        Full-screen apps, Win+D/Alt+Tab juggling and other topmost windows can push
        the star behind; SetWindowPos with SWP_NOACTIVATE never steals focus.
        """

        if getattr(self, "_keeper", None) is not None:
            return

        def loop() -> None:
            import ctypes

            user32 = ctypes.windll.user32
            while not self.quitting and self.window is not None:
                time.sleep(3)
                if not self.visible:
                    continue
                try:
                    hwnd = self._hwnd()
                    if hwnd:
                        # HWND_TOPMOST=-1; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_SHOWWINDOW
                        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)
                except Exception:
                    pass

        self._keeper = threading.Thread(target=loop, name="mascot-topmost", daemon=True)
        self._keeper.start()

    def _hwnd(self) -> int:
        cached = getattr(self, "_hwnd_cache", 0)
        if cached:
            return cached
        try:
            from System import Func, Int64  # type: ignore[import-not-found]

            form = self.window.native
            value = int(form.Invoke(Func[Int64](lambda: form.Handle.ToInt64())))
        except Exception:
            value = 0
        self._hwnd_cache = value
        return value

    def hide(self) -> None:
        with self._lock:
            if self.window is None:
                return
            self.window.hide()
            self.visible = False

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.settings.set_enabled(enabled)
        self._last_enabled = bool(enabled)
        if enabled:
            self.show()
        else:
            self.hide()
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                log.exception("maskot değişikliği bildirilemedi")

    def sync_from_settings(self) -> None:
        """Apply an on/off change written by someone else (the panel's Ayarlar).

        Only reacts to *changes* of the persisted switch, so a session-only
        "Şimdilik gizle" is not undone by the periodic poll.
        """

        enabled = self.settings.enabled
        last = getattr(self, "_last_enabled", None)
        self._last_enabled = enabled
        if last is None or enabled == last or not self.started.is_set():
            return
        if enabled:
            self.show()
        else:
            self.hide()
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                log.exception("maskot değişikliği bildirilemedi")

    def toggle(self) -> bool:
        """Flip "Maskotu göster/gizle"; returns the new state."""

        if self.settings.enabled and not self.visible:
            # Hidden for this session only ("Şimdilik gizle"): bring it back.
            self.show()
            return True
        new = not self.settings.enabled
        self.set_enabled(new)
        return new

    def destroy(self) -> None:
        with self._lock:
            self.quitting = True
            if self._save_timer is not None:
                self._save_timer.cancel()
            if self.window is None:
                return
            try:
                self.window.destroy()
            except Exception:
                log.exception("maskot kapatılamadı")

    # -- js_api -------------------------------------------------------------
    def open_main(self) -> bool:
        if self._on_open_main is None:
            return False
        try:
            self._on_open_main()
            return True
        except Exception:
            log.exception("ana pencere açılamadı")
            return False

    def set_expanded(self, expanded: bool) -> bool:
        with self._lock:
            if expanded == self.expanded:
                return True
            self.expanded = expanded
            if self.window is None or not self.visible:
                return True
            width, height = size_for(expanded)
            try:
                from webview.window import FixPoint

                fix = FixPoint.SOUTH | FixPoint.EAST
            except Exception:
                fix = None
            try:
                if fix is not None:
                    self.window.resize(width, height, fix)
                else:
                    geo = self.geometry(expanded)
                    self.window.resize(width, height)
                    self.window.move(geo.x, geo.y)
            except Exception:
                log.exception("maskot boyutu değiştirilemedi")
                return False
            return True

    def set_hit_regions(self, regions: Any) -> bool:
        """Clickable areas of the page (see module docstring)."""

        self._hit_regions = regions if isinstance(regions, list) else []
        return self._paint_hit_mask()

    def _paint_hit_mask(self) -> bool:
        window = self.window
        try:
            form = window.native if window is not None else None
        except Exception:
            form = None
        if form is None:
            return False
        try:
            import ctypes

            from System import Func, Type  # type: ignore[import-not-found]
            from System.Drawing import Bitmap, Color, Graphics, Rectangle, SolidBrush  # type: ignore[import-not-found]

            rgb = tuple(int(KEY_COLOR[i:i + 2], 16) for i in (1, 3, 5))
            regions = self._hit_regions

            def paint() -> None:
                size = form.ClientSize
                if size.Width <= 0 or size.Height <= 0:
                    return
                scale = ctypes.windll.user32.GetDpiForWindow(form.Handle.ToInt32()) / 96 or 1.0
                bmp = Bitmap(size.Width, size.Height)
                g = Graphics.FromImage(bmp)
                try:
                    g.Clear(Color.FromArgb(255, *rgb))
                    star = SolidBrush(Color.FromArgb(255, 0xF5, 0xC4, 0x51))
                    panel = SolidBrush(Color.FromArgb(255, 0xFF, 0xFD, 0xF8))
                    for shape in hit_shapes(regions, scale, (size.Width, size.Height)):
                        rect = Rectangle(shape.x, shape.y, shape.width, shape.height)
                        if shape.kind == "circle":
                            g.FillEllipse(star, rect)
                        else:
                            g.FillRectangle(panel, rect)
                finally:
                    g.Dispose()
                old = form.BackgroundImage
                form.BackgroundImage = bmp
                form.Invalidate()
                if old is not None:
                    old.Dispose()

            form.Invoke(Func[Type](paint))
            return True
        except Exception as exc:
            log.info("maskot tıklama alanı çizilemedi: %s", exc)
            return False

    # -- events -------------------------------------------------------------
    def _on_closing(self) -> bool:
        if self.quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def _save_now(self) -> None:
        self._save_timer = None
        window = self.window
        if window is None or not self.visible or self.quitting:
            return
        try:
            x, y = int(window.x), int(window.y)
            width, height = int(window.width), int(window.height)
        except Exception:
            return
        if width <= 0 or height <= 0:
            return
        self.settings.save_anchor(x + width, y + height)

    def _schedule_save(self, *_args: Any) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.8, self._save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _on_resized(self, *_args: Any) -> None:
        # The mask bitmap is sized to the client area: redraw it for the new size.
        if self._hit_regions:
            self._paint_hit_mask()

    def _on_loaded(self, *_args: Any) -> None:
        if self.visible:
            self._apply_style()
        if not self._mic_hooked:
            try:
                from .desktop_window import grant_microphone

                self._mic_hooked = grant_microphone(self.window)
            except Exception:
                self._mic_hooked = False

    def _apply_style(self) -> None:
        """Colour-key transparency + no taskbar button (WS_EX_TOOLWINDOW)."""

        if self.window is None:
            return
        try:
            form = self.window.native
        except Exception:
            form = None
        if form is None:
            return
        try:
            import ctypes

            from System import Func, Type  # type: ignore[import-not-found]
            from System.Drawing import Color  # type: ignore[import-not-found]

            user32 = ctypes.windll.user32
            rgb = tuple(int(KEY_COLOR[i:i + 2], 16) for i in (1, 3, 5))

            colorref = rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)  # 0x00BBGGRR

            def style() -> None:
                key = Color.FromArgb(255, *rgb)
                # WebView2's own (GPU) background is never exactly the key colour,
                # so it must be transparent and let the GDI-painted form show through.
                form.browser.webview.DefaultBackgroundColor = Color.Transparent
                form.BackColor = key
                form.TransparencyKey = key
                hwnd = form.Handle.ToInt32()
                ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                wanted = (ex | 0x80000 | 0x80) & ~0x40000  # +LAYERED +TOOLWINDOW -APPWINDOW
                if wanted != ex:
                    # The taskbar re-reads the style when the window is shown again.
                    rehide = bool(not ex & 0x80 and form.Visible)
                    if rehide:
                        form.Hide()
                    user32.SetWindowLongW(hwnd, -20, wanted)
                    if rehide:
                        form.Show()
                # Belt and braces: the colour key itself, straight through Win32.
                user32.SetLayeredWindowAttributes(hwnd, colorref, 0, 0x1)  # LWA_COLORKEY

            form.Invoke(Func[Type](style))
            if not self._styled:
                log.info("maskot saydamlığı uygulandı (renk anahtarı %s)", KEY_COLOR)
            self._styled = True
        except Exception as exc:
            log.info("maskot saydamlığı uygulanamadı: %s", exc)


# ---------------------------------------------------------------------------
# Module-level entry points for tray/app.py
# ---------------------------------------------------------------------------
_MASCOT: MascotWindow | None = None


def create_mascot_window(base_url: str | None, settings: MascotSettings | Path,
                         on_open_main: Callable[[], Any] | None = None,
                         webview_module: Any = None,
                         on_change: Callable[[], None] | None = None) -> MascotWindow:
    """Create the hidden mascot window; it appears once ``set_backend`` gets a URL."""

    global _MASCOT
    store = settings if isinstance(settings, MascotSettings) else MascotSettings(Path(settings))
    mascot = MascotWindow(store, on_open_main, webview_module=webview_module, on_change=on_change)
    mascot.create(base_url)
    _MASCOT = mascot
    return mascot


def current_mascot() -> MascotWindow | None:
    return _MASCOT


def toggle_mascot() -> bool | None:
    """Flip show/hide (persisted). None when no mascot window exists."""

    return _MASCOT.toggle() if _MASCOT is not None else None
