"""Polaris tray application: controller (testable) + pystray UI + smoke mode."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

from . import APP_NAME
from .autostart import Autostart, launcher_command
from .backend_proc import BackendProcess
from .health import (
    NO_KEY, RUNNING, STARTING, HealthInfo, Status, compute_status, fetch_health,
    find_free_port, port_in_use,
)
from .paths import Paths, get_paths
from .settings import FALLBACK_PORTS, BackendAddress, ensure_env_file, resolve_address
from . import ui

log = logging.getLogger("tray")

POLL_S = 5.0
POLL_STARTING_S = 1.0
READY_TOAST = "Polaris hazır — Brave'de paneli aç"
READY_TOAST_WINDOW = "Polaris hazır — {hotkey} ile pencereyi aç"
#: Autostart (Windows logon) starts in the tray only; shortcuts open the window.
HIDDEN_FLAG = "--hidden"


def setup_logging(paths: Paths) -> None:
    root = logging.getLogger("tray")
    if any(getattr(h, "_polaris_tray", False) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    try:
        paths.logs.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(paths.tray_log, maxBytes=1_000_000,
                                                       backupCount=2, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._polaris_tray = True  # type: ignore[attr-defined]
    root.addHandler(handler)


class Controller:
    """All tray behaviour that does not need a GUI."""

    def __init__(self, paths: Paths, address: BackendAddress | None = None,
                 backend: BackendProcess | None = None,
                 fetch: Callable[[str], HealthInfo | None] = fetch_health,
                 port_check: Callable[[str, int], bool] = port_in_use,
                 alert: Callable[[str], None] | None = None,
                 notify: Callable[[str], None] | None = None) -> None:
        self.paths = paths
        self.address = address or resolve_address(paths.env_file)
        self.backend = backend or BackendProcess(paths, self.address)
        self._fetch = fetch
        self._port_check = port_check
        self._alert = alert or (lambda text: ui.message_box(text, icon=ui.MB_ICONWARNING))
        self._notify = notify or (lambda text: None)
        self.wanted_running = False
        self.external = False  # a Polaris backend we did not start is serving the port
        self.error = ""
        self.notified_ready = False
        self.status = Status("stopped", "Polaris · durdu", "Durum: durdu")
        self._lock = threading.RLock()

    # -- actions --------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            self.error = ""
            self.wanted_running = True
            if self.backend.is_alive():
                return
            addr = self.address
            if self._port_check(addr.probe_host, addr.port):
                info = self._fetch(addr.health_url)
                if info is not None and info.polaris:
                    log.info("port %s zaten bir Polaris backend'inde; ona bağlanılıyor", addr.port)
                    self.external = True
                    return
                alt = self._free_candidate(addr)
                if alt is None:
                    self.error = f"port {addr.port} dolu"
                    self.wanted_running = False
                    log.warning("port %s başka bir program tarafından kullanılıyor", addr.port)
                    self._alert(ui.PORT_BUSY.format(port=addr.port))
                    return
                log.warning("port %s dolu; Polaris %s portunda başlatılıyor (panel kendisi bulur)",
                            addr.port, alt.port)
                self.address = alt
                self.backend.address = alt
            self.external = False
            try:
                self.backend.start()
            except OSError as exc:
                self.error = "sunucu başlatılamadı"
                self.wanted_running = False
                log.exception("backend başlatılamadı")
                self._alert(f"Polaris sunucusu başlatılamadı:\n{exc}\n\nKurulum (.venv) tamam mı? "
                            f"Ayrıntı: {self.paths.backend_log}")

    def _free_candidate(self, addr: BackendAddress) -> BackendAddress | None:
        """Next free port from the list the panel also probes (FALLBACK_PORTS)."""

        for port in FALLBACK_PORTS:
            if port != addr.port and not self._port_check(addr.probe_host, port):
                return BackendAddress(addr.host, port)
        return None

    def stop(self) -> None:
        with self._lock:
            self.wanted_running = False
            self.external = False
            self.error = ""
            self.backend.stop()

    def restart(self) -> None:
        with self._lock:
            self.stop()
            # PORT/HOST may have been edited in backend/.env in the meantime.
            self.address = resolve_address(self.paths.env_file)
            self.backend.address = self.address
            self.start()

    def toggle_running(self) -> None:
        if self.is_running():
            self.stop()
        else:
            self.start()

    def is_running(self) -> bool:
        return self.wanted_running or self.external or self.backend.is_alive()

    # -- polling --------------------------------------------------------------
    def poll(self) -> Status:
        with self._lock:
            active = self.wanted_running or self.external or self.backend.is_alive()
            info = self._fetch(self.address.health_url) if active else None
            if self.external and info is None:
                self.external = False
                self.wanted_running = self.backend.is_alive()
            alive = self.backend.is_alive() or self.external
            status = compute_status(info, process_alive=alive, wanted_running=self.wanted_running or self.external,
                                    error=self.error, port=self.address.port)
            if status.state in (RUNNING, NO_KEY) and not self.notified_ready:
                self.notified_ready = True
                self._notify(READY_TOAST)
            self.status = status
            return status

    def poll_interval(self) -> float:
        return POLL_STARTING_S if self.status.state == STARTING else POLL_S

    def shutdown(self) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
#: The running TrayApp (one per process); lets other windows (e.g. a mascot
#: window created with webview.create_window before/after webview.start) reach
#: the main Polaris window without importing TrayApp internals.
_CURRENT: "TrayApp | None" = None


def show_main_window() -> bool:
    """Show (and focus) the main Polaris window. False when there is none (tray-only)."""

    app = _CURRENT
    if app is None or app.window is None:
        return False
    app.window.show()
    return True


def toggle_main_window() -> bool:
    app = _CURRENT
    if app is None or app.window is None:
        return False
    app.window.toggle()
    return True


def current_backend_url() -> str | None:
    """Base URL of the backend the tray manages (``http://127.0.0.1:<port>``)."""

    app = _CURRENT
    return app.ctrl.address.base_url if app is not None else None


class TrayApp:
    def __init__(self, paths: Paths) -> None:
        global _CURRENT
        import pystray

        from .icons import make_icon

        self.pystray = pystray
        self.make_icon = make_icon
        self.paths = paths
        self.autostart = Autostart(f"{launcher_command(paths)} {HIDDEN_FLAG}")
        self.ctrl = Controller(paths, notify=self._toast)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.window: Any = None  # DesktopWindow once pywebview + WebView2 are usable
        self.mascot: Any = None  # tray/mascot.py MascotWindow (desktop star)
        self.hotkey: Any = None
        self.show_listener: Any = None
        self.icon = pystray.Icon(APP_NAME, make_icon("grey"), "Polaris · başlıyor…", self._menu())
        _CURRENT = self

    # -- menu -----------------------------------------------------------------
    def _menu(self) -> Any:
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        return Menu(
            # default=True: a left click on the tray icon runs this item.
            Item("Polaris'i aç", self._safe(self.toggle_window), default=True),
            Item(lambda _i: self.ctrl.status.detail, None, enabled=False),
            Menu.SEPARATOR,
            Item("Sunucuyu yeniden başlat", self._safe(self.ctrl.restart)),
            Item(lambda _i: "Sunucuyu durdur" if self.ctrl.is_running() else "Sunucuyu başlat",
                 self._safe(self.ctrl.toggle_running)),
            Menu.SEPARATOR,
            Item("Ayarlar dosyasını aç", self._safe(self.open_settings)),
            Item("Kayıtları aç", self._safe(self.open_logs)),
            Item("Eklenti klasörünü aç", self._safe(self.open_extension)),
            Item("Brave'e yükleme adımları", self._safe(self.show_brave_steps)),
            Menu.SEPARATOR,
            Item("Maskotu göster/gizle", self._safe(self.toggle_mascot),
                 checked=lambda _i: self._mascot_enabled(),
                 enabled=lambda _i: self.mascot is not None),
            Item("Windows açılışında başlat", self._safe(self.toggle_autostart),
                 checked=lambda _i: self._autostart_enabled()),
            Menu.SEPARATOR,
            Item("Çıkış", self.quit),
        )

    def _safe(self, fn: Callable[[], None]) -> Callable[..., None]:
        def run(*_args: Any) -> None:
            def work() -> None:
                try:
                    fn()
                except Exception as exc:  # never let a menu click kill the tray
                    log.exception("menü eylemi başarısız")
                    ui.message_box(f"İşlem başarısız: {exc}", icon=ui.MB_ICONERROR)
                finally:
                    self._wake.set()
            threading.Thread(target=work, daemon=True).start()
        return run

    def _mascot_enabled(self) -> bool:
        try:
            return self.mascot is not None and bool(self.mascot.enabled)
        except Exception:
            return False

    def toggle_mascot(self) -> None:
        if self.mascot is not None:
            self.mascot.toggle()
        self.icon.update_menu()

    def _autostart_enabled(self) -> bool:
        try:
            return self.autostart.is_enabled()
        except Exception:
            return False

    # -- actions --------------------------------------------------------------
    def open_settings(self) -> None:
        created = ensure_env_file(self.paths.env_file, self.paths.env_example)
        ui.open_in_notepad(self.paths.env_file)
        if created:
            ui.message_box("backend\\.env oluşturuldu. DEEPSEEK_API_KEY değerini yaz, kaydet ve "
                           "menüden 'Sunucuyu yeniden başlat'ı seç.")

    def open_logs(self) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        ui.open_in_explorer(self.paths.logs)

    def open_extension(self) -> None:
        if not self.paths.extension_dist.is_dir():
            ui.message_box("Eklenti henüz derlenmemiş (extension\\dist yok).\n"
                           "Derlemek için: scripts\\build-extension.ps1", icon=ui.MB_ICONWARNING)
            return
        ui.open_in_explorer(self.paths.extension_dist)

    def show_brave_steps(self) -> None:
        ui.message_box(ui.BRAVE_STEPS.format(dist=self.paths.extension_dist,
                                             url=f"http://localhost:{self.ctrl.address.port}"))

    def toggle_autostart(self) -> None:
        self.autostart.toggle()
        self.icon.update_menu()

    def show_window(self) -> None:
        if self.window is None:
            self.show_brave_steps()
            return
        self.window.show()

    def toggle_window(self) -> None:
        if self.window is None:
            self.show_brave_steps()
            return
        self.window.toggle()

    def quit(self, *_args: Any) -> None:
        log.info("çıkış")
        self._stop.set()
        self._wake.set()
        for helper in (self.hotkey, self.show_listener):
            try:
                if helper is not None:
                    helper.stop()
            except Exception:
                log.exception("yardımcı durdurulamadı")
        try:
            self.ctrl.shutdown()
        finally:
            self.icon.stop()
            if self.mascot is not None:
                self.mascot.destroy()
            if self.window is not None:
                self.window.destroy()

    def _toast(self, text: str) -> None:
        if text == READY_TOAST and self.window is not None:
            label = self.hotkey.label if self.hotkey is not None and self.hotkey.label else "tepsi simgesi"
            text = READY_TOAST_WINDOW.format(hotkey=label)
        try:
            self.icon.notify(text, APP_NAME)
        except Exception:  # pragma: no cover - notifications disabled
            log.warning("bildirim gösterilemedi")

    # -- loop -----------------------------------------------------------------
    def _apply(self, status: Status) -> None:
        self.icon.icon = self.make_icon(status.color)
        self.icon.title = status.tooltip
        self.icon.update_menu()
        if self.window is not None and status.state in (RUNNING, NO_KEY):
            self.window.set_backend(self.ctrl.address.base_url)
            if self.mascot is not None:
                self.mascot.set_backend(self.ctrl.address.base_url)

    def _poll_loop(self) -> None:
        last: Status | None = None
        while not self._stop.is_set():
            try:
                status = self.ctrl.poll()
                if status != last:
                    self._apply(status)
                    log.info("durum: %s", status.detail)
                    last = status
            except Exception:
                log.exception("durum güncellenemedi")
            if self.mascot is not None:
                try:
                    self.mascot.sync_from_settings()  # panel Ayarlar -> Masaüstü maskotu
                except Exception:
                    log.exception("maskot ayarı uygulanamadı")
            self._wake.wait(self.ctrl.poll_interval())
            self._wake.clear()

    def _setup(self, icon: Any) -> None:
        icon.visible = True
        if self.autostart.is_enabled() and not self.autostart.is_current():
            try:  # install moved: keep the Run entry pointing at this launcher
                self.autostart.enable()
            except OSError:
                pass
        self.ctrl.start()
        threading.Thread(target=self._poll_loop, daemon=True, name="polaris-poll").start()

    # -- desktop window -----------------------------------------------------
    def _make_window(self) -> Any:
        """DesktopWindow, or None (tray-only) when pywebview/WebView2 is missing."""

        from . import desktop_window as dw

        if not dw.webview2_installed():
            log.warning("WebView2 Runtime yok; pencere kapalı, yalnızca tepsi")
            ui.message_box(dw.WEBVIEW2_MISSING, icon=ui.MB_ICONWARNING)
            return None
        try:
            import webview  # noqa: F401
        except Exception as exc:
            log.warning("pywebview yüklenemedi: %s", exc)
            ui.message_box(dw.WEBVIEW_MISSING, icon=ui.MB_ICONWARNING)
            return None
        try:
            if not self.paths.app_icon.is_file():
                from .icons import write_ico

                write_ico(self.paths.app_icon)
        except Exception:
            log.info("pencere ikonu yazılamadı")
        debug = os.environ.get("POLARIS_WEBVIEW_DEBUG_PORT", "").strip()
        window = dw.DesktopWindow(dw.SettingsStore(self.paths.tray_settings), self.paths.webview_data,
                                  icon_path=self.paths.app_icon,
                                  debug_port=int(debug) if debug.isdigit() else None)
        window.create()
        self._make_mascot(window)
        return window

    def _make_mascot(self, window: Any) -> None:
        """Desktop star next to the main window; never fatal."""

        try:
            from .mascot import create_mascot_window

            self.mascot = create_mascot_window(None, self.paths.tray_settings,
                                               on_open_main=window.show,
                                               on_change=self.icon.update_menu)
        except Exception:
            log.exception("maskot oluşturulamadı")
            self.mascot = None

    def _start_window_helpers(self) -> None:
        from .hotkey import HotkeyListener
        from .single_instance import ShowSignalListener

        self.hotkey = HotkeyListener(lambda: self.window and self.window.toggle())
        if self.hotkey.start() is None:
            log.warning("genel kısayol yok; pencere tepsi simgesinden açılır")
        self.show_listener = ShowSignalListener(lambda: self.window and self.window.show())
        if not self.show_listener.start():
            log.info("ikinci başlatma sinyali dinlenemiyor")

    def _window_started(self, show: bool) -> None:
        window = self.window
        if self.mascot is not None:
            self.mascot.mark_started()
        if self.ctrl.status.state in (RUNNING, NO_KEY):
            window.set_backend(self.ctrl.address.base_url)
            if self.mascot is not None:
                self.mascot.set_backend(self.ctrl.address.base_url)
        if show:
            window.show()
        window.flush_pending_show()

    def run(self, show_window: bool = True) -> None:
        try:
            self.window = self._make_window()
        except Exception:
            log.exception("pencere oluşturulamadı; yalnızca tepsi")
            self.window = None
        if self.window is None:
            try:
                self.icon.run(setup=self._setup)
            finally:
                self.ctrl.shutdown()
            return
        self._start_window_helpers()
        # pywebview needs the main thread: the tray icon runs on its own thread.
        self.icon.run_detached(setup=self._setup)
        try:
            self.window.run(on_started=lambda: self._window_started(show_window))
        finally:
            self._stop.set()
            self._wake.set()
            for helper in (self.hotkey, self.show_listener):
                try:
                    if helper is not None:
                        helper.stop()
                except Exception:
                    pass
            self.ctrl.shutdown()
            try:
                self.icon.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Smoke mode
# ---------------------------------------------------------------------------
def _out(text: str) -> None:
    log.info(text)
    if sys.stdout is not None:
        try:
            print(text, flush=True)
        except Exception:
            pass


def smoke(port: int | None = None, timeout: float = 120.0) -> int:
    """Start the backend exactly like the tray does, wait for /health ok, stop it."""

    paths = get_paths()
    setup_logging(paths)
    from .icons import make_icon

    for color in ("gold", "grey", "red"):
        make_icon(color)
    import pystray  # noqa: F401 - import check only

    port = port or find_free_port()
    address = BackendAddress("127.0.0.1", port)
    backend = BackendProcess(paths, address)
    _out(f"[smoke] kök: {paths.root}")
    _out(f"[smoke] komut: {' '.join(backend.command())}")
    started = time.monotonic()
    backend.start()
    ok = False
    info: HealthInfo | None = None
    try:
        while time.monotonic() - started < timeout:
            if not backend.is_alive():
                _out(f"[smoke] HATA: backend kapandı (çıkış kodu {backend.exit_code()}); bkz. {paths.backend_log}")
                break
            info = fetch_health(address.health_url, timeout=1.5)
            if info is not None and info.ok:
                ok = True
                break
            time.sleep(0.5)
        if ok and info is not None:
            status = compute_status(info, process_alive=True, wanted_running=True, port=port)
            _out(f"[smoke] /health ok {time.monotonic() - started:.1f}s · sürüm {info.version or '?'} · "
                 f"model {info.model or '?'} · llm_configured={info.llm_configured} · stt_ready={info.stt_ready}")
            _out(f"[smoke] ipucu: {status.tooltip}")
        elif not ok:
            _out(f"[smoke] HATA: {timeout:.0f}s içinde /health ok gelmedi")
    finally:
        backend.stop()
    # Windows may need a moment to release the listening socket after the process exits.
    deadline = time.monotonic() + 5.0
    while port_in_use("127.0.0.1", port) and time.monotonic() < deadline:
        time.sleep(0.2)
    stopped = not backend.is_alive() and not port_in_use("127.0.0.1", port)
    _out(f"[smoke] durduruldu: {'evet' if stopped else 'HAYIR'}")
    return 0 if ok and stopped else 1


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tray", description="Polaris sistem tepsisi")
    parser.add_argument("--smoke", action="store_true",
                        help="backend'i boş bir portta başlat, /health bekle, durdur, çık")
    parser.add_argument("--port", type=int, default=None, help="smoke için port (varsayılan: boş port)")
    parser.add_argument("--timeout", type=float, default=120.0, help="smoke zaman aşımı (sn)")
    parser.add_argument(HIDDEN_FLAG, action="store_true",
                        help="pencereyi açmadan yalnızca tepside başla (Windows açılışı)")
    args = parser.parse_args(argv)

    if args.smoke:
        return smoke(args.port, args.timeout)

    from .single_instance import SingleInstance, signal_show

    paths = get_paths()
    setup_logging(paths)
    guard = SingleInstance(lock_dir=paths.logs)
    if not guard.acquire():
        if args.hidden:
            return 0
        if signal_show():
            log.info("Polaris zaten çalışıyor; penceresi gösterildi")
            return 0
        ui.message_box("Polaris zaten çalışıyor — sistem tepsisindeki ★ simgesine bak.", wait=True)
        return 0
    try:
        log.info("tray başlıyor (kök %s)", paths.root)
        TrayApp(paths).run(show_window=not args.hidden)
    except Exception as exc:
        log.exception("tray çöktü")
        ui.message_box(f"Polaris tepsi uygulaması beklenmedik şekilde kapandı:\n{exc}\n\n"
                       f"Ayrıntı: {paths.tray_log}", icon=ui.MB_ICONERROR, wait=True)
        return 1
    finally:
        guard.release()
    return 0
