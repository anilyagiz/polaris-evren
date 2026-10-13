"""Polaris desktop window helpers: geometry/settings, hotkey, show signal, window wrapper."""

from __future__ import annotations

import json
import os
import threading
import uuid
from types import SimpleNamespace

import pytest

from tray import app as tray_app
from tray import desktop_window as dw
from tray import hotkey as hk
from tray.single_instance import ShowSignalListener, signal_show


# -- geometry + settings file ----------------------------------------------------
WORK = dw.Rect(0, 0, 1920, 1040)


def test_first_run_is_centered_default_size():
    geo = dw.initial_geometry(None, WORK)
    assert (geo.width, geo.height) == dw.DEFAULT_SIZE
    assert geo.x == (1920 - 440) // 2 and geo.y == (1040 - 760) // 2


def test_saved_geometry_is_restored_and_min_size_enforced():
    geo = dw.initial_geometry({"x": 100, "y": 50, "width": 500, "height": 800}, WORK)
    assert geo == dw.Geometry(500, 800, 100, 50)
    small = dw.initial_geometry({"x": 10, "y": 10, "width": 50, "height": 60}, WORK)
    assert (small.width, small.height) == dw.MIN_SIZE


@pytest.mark.parametrize("saved", [
    {"x": 5000, "y": 50, "width": 440, "height": 760},  # monitor unplugged
    {"x": 100, "y": -900, "width": 440, "height": 760},
    {"x": "a", "y": 0, "width": 440, "height": 760},
    {"y": 0},
])
def test_offscreen_or_broken_saved_geometry_recenters(saved):
    geo = dw.initial_geometry(saved, WORK)
    assert 0 <= geo.x <= 1920 - geo.width and 0 <= geo.y <= 1040 - geo.height


def test_small_screen_shrinks_default():
    geo = dw.centered(dw.Rect(0, 0, 1024, 600))
    assert geo.height <= 600 and geo.height >= dw.MIN_SIZE[1]


def test_settings_store_roundtrip_keeps_other_keys(tmp_path):
    path = tmp_path / "data" / "tray-settings.json"
    store = dw.SettingsStore(path)
    assert store.window() is None
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"other": 1}), encoding="utf-8")
    store.save_window(dw.Geometry(450, 700, 10, 20))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"other": 1, "window": {"x": 10, "y": 20, "width": 450, "height": 700}}
    assert store.window() == {"x": 10, "y": 20, "width": 450, "height": 700}
    path.write_text("{bozuk", encoding="utf-8")
    assert store.window() is None


def test_webview2_detection_uses_registry_reader():
    seen = []

    def none(hive, path):
        seen.append((hive, path))
        return None

    assert dw.webview2_installed(none) is False
    assert any("F3017226" in p for _, p in seen)
    assert dw.webview2_installed(lambda h, p: "0.0.0.0") is False
    assert dw.webview2_installed(lambda h, p: "155.0.1.2" if h == "HKCU" else None) is True


def test_webview2_missing_message_has_bootstrapper_link():
    assert dw.WEBVIEW2_URL in dw.WEBVIEW2_MISSING
    assert "WebView2" in dw.WEBVIEW2_MISSING


def test_app_url():
    assert dw.app_url("http://127.0.0.1:8766") == "http://127.0.0.1:8766/app/"
    assert dw.app_url("http://127.0.0.1:8766/") == "http://127.0.0.1:8766/app/"


# -- hotkey (mock user32) ---------------------------------------------------------
class FakeUser32:
    def __init__(self, accept):
        self.accept = set(accept)
        self.calls = []
        self.unregistered = []
        self.posted = []

    def RegisterHotKey(self, hwnd, ident, mods, vk):  # noqa: N802
        self.calls.append((mods & ~hk.MOD_NOREPEAT, vk, bool(mods & hk.MOD_NOREPEAT)))
        return (mods & ~hk.MOD_NOREPEAT) in self.accept

    def UnregisterHotKey(self, hwnd, ident):  # noqa: N802
        self.unregistered.append(ident)
        return True

    def PostThreadMessageW(self, tid, msg, w, l):  # noqa: N802
        self.posted.append((tid, msg))
        return True


def test_alt_space_registered_first():
    user32 = FakeUser32({hk.MOD_ALT})
    key = hk.register_first(user32)
    assert key is not None and key.label == "Alt+Space"
    assert user32.calls == [(hk.MOD_ALT, hk.VK_SPACE, True)]


def test_falls_back_to_ctrl_alt_space(caplog):
    user32 = FakeUser32({hk.MOD_CONTROL | hk.MOD_ALT})
    with caplog.at_level("WARNING", logger="tray.hotkey"):
        key = hk.register_first(user32)
    assert key is not None and key.label == "Ctrl+Alt+Space"
    assert [c[0] for c in user32.calls] == [hk.MOD_ALT, hk.MOD_CONTROL | hk.MOD_ALT]
    assert "Ctrl+Alt+Space" in caplog.text


def test_no_hotkey_available():
    assert hk.register_first(FakeUser32(set())) is None


def test_listener_thread_registers_fires_and_stops(monkeypatch):
    user32 = FakeUser32({hk.MOD_ALT})
    fired = threading.Event()
    stop = threading.Event()

    def fake_loop(self, _user32):
        self.fire()  # one WM_HOTKEY
        stop.wait(2)

    monkeypatch.setattr(hk.HotkeyListener, "loop", fake_loop)
    listener = hk.HotkeyListener(fired.set, user32=user32, thread_id=lambda: 4242)
    assert listener.start().label == "Alt+Space"
    assert fired.wait(2)
    listener.stop()
    stop.set()
    listener._thread.join(2)
    assert user32.posted == [(4242, hk.WM_QUIT)]
    assert user32.unregistered == [hk.HOTKEY_ID]


def test_listener_callback_errors_are_swallowed():
    listener = hk.HotkeyListener(lambda: 1 / 0, user32=FakeUser32(set()))
    listener.fire()  # must not raise


# -- show signal (mock kernel32 + real named event) -------------------------------
class FakeEvents:
    def __init__(self):
        self.events = {}
        self.closed = []

    def CreateEventW(self, sa, manual, initial, name):  # noqa: N802
        self.events[name] = {"set": False}
        return name

    def OpenEventW(self, access, inherit, name):  # noqa: N802
        return name if name in self.events else 0

    def SetEvent(self, handle):  # noqa: N802
        self.events[handle]["set"] = True
        return True

    def WaitForSingleObject(self, handle, ms):  # noqa: N802
        if self.events[handle]["set"]:
            self.events[handle]["set"] = False
            return 0
        return 0x102  # WAIT_TIMEOUT

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)


def test_show_signal_with_fake_kernel32():
    k32 = FakeEvents()
    assert signal_show("ev", kernel32=k32) is False  # nobody listening
    shown = []
    listener = ShowSignalListener(lambda: shown.append(1), name="ev", kernel32=k32)
    listener._handle = k32.CreateEventW(None, False, False, "ev")
    assert listener.wait_once() is False
    assert signal_show("ev", kernel32=k32) is True
    assert listener.wait_once() is True and shown == [1]
    assert listener.wait_once() is False  # auto-reset


@pytest.mark.skipif(os.name != "nt", reason="named events are Windows-only")
def test_show_signal_real_named_event():
    name = f"Local\\PolarisTest-{uuid.uuid4().hex}"
    shown = threading.Event()
    listener = ShowSignalListener(shown.set, name=name, poll_ms=50)
    assert listener.start()
    try:
        assert signal_show(name) is True
        assert shown.wait(2)
    finally:
        listener.stop()
    assert signal_show(name) is False


# -- DesktopWindow with a fake pywebview ------------------------------------------
class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *args):
        return [h(*args) for h in self.handlers]


class FakeWindow:
    def __init__(self, **kw):
        self.kw = kw
        self.events = SimpleNamespace(closing=FakeEvent(), moved=FakeEvent(), resized=FakeEvent(),
                                      loaded=FakeEvent())
        self.loaded_urls = []
        self.shown = 0
        self.hidden = 0
        self.destroyed = False
        self.width, self.height, self.x, self.y = kw["width"], kw["height"], kw["x"], kw["y"]

    def load_url(self, url):
        self.loaded_urls.append(url)

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def restore(self):
        pass

    def destroy(self):
        self.destroyed = True


class FakeWebview:
    def __init__(self):
        self.settings = {}
        self.windows = []
        self.start_kwargs = None

    def create_window(self, title, **kw):
        window = FakeWindow(title=title, **kw)
        self.windows.append(window)
        return window

    def start(self, func, **kw):
        self.start_kwargs = kw
        func()


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(dw, "work_area", lambda: WORK)
    monkeypatch.setattr(dw, "virtual_screen", lambda: None)
    monkeypatch.setattr(dw, "_bring_to_front", lambda title: None)
    monkeypatch.setattr(dw, "_is_foreground", lambda title: True)
    monkeypatch.setattr(dw, "grant_microphone", lambda window, url: True)
    fake = FakeWebview()
    window = dw.DesktopWindow(dw.SettingsStore(tmp_path / "tray-settings.json"), tmp_path / "webview",
                              webview_module=fake, debug_port=9333)
    window.create()
    return window, fake


def test_window_created_hidden_with_size_and_min(desktop):
    window, fake = desktop
    kw = fake.windows[0].kw
    assert kw["hidden"] is True and kw["min_size"] == dw.MIN_SIZE
    assert (kw["width"], kw["height"]) == dw.DEFAULT_SIZE
    assert fake.windows[0].kw["title"] == "Polaris"
    assert fake.settings["REMOTE_DEBUGGING_PORT"] == 9333


def test_show_before_start_is_deferred_then_loads_backend(desktop):
    window, fake = desktop
    window.show()
    assert fake.windows[0].shown == 0
    started = []
    window.run(on_started=lambda: started.append(1))
    assert fake.start_kwargs["private_mode"] is False and fake.start_kwargs["gui"] == "edgechromium"
    window.set_backend("http://127.0.0.1:8766")
    window.flush_pending_show()
    w = fake.windows[0]
    assert started == [1] and w.shown == 1 and window.visible
    assert w.loaded_urls == ["http://127.0.0.1:8766/app/"]
    window.set_backend("http://127.0.0.1:8766")  # same url: no reload
    window.set_backend("http://127.0.0.1:8767")  # port moved: reload
    assert w.loaded_urls[-1] == "http://127.0.0.1:8767/app/" and len(w.loaded_urls) == 2


def test_close_button_hides_and_saves_geometry(desktop, tmp_path):
    window, fake = desktop
    window.run()
    window.show()
    w = fake.windows[0]
    w.x, w.y, w.width, w.height = 30, 40, 500, 700
    results = w.events.closing.fire()
    assert results == [False]  # close cancelled
    for _ in range(50):
        if w.hidden:
            break
        threading.Event().wait(0.02)
    assert w.hidden == 1 and not window.visible
    saved = json.loads((tmp_path / "tray-settings.json").read_text(encoding="utf-8"))
    assert saved["window"] == {"x": 30, "y": 40, "width": 500, "height": 700}


def test_toggle_and_destroy(desktop):
    window, fake = desktop
    window.run()
    w = fake.windows[0]
    window.toggle()
    assert window.visible and w.shown == 1
    window.toggle()
    assert not window.visible and w.hidden == 1
    window.destroy()
    assert w.destroyed and w.events.closing.fire() == [True]  # quitting: real close


# -- tray main: second launch -----------------------------------------------------
def test_second_launch_signals_show(monkeypatch):
    class Busy:
        def __init__(self, **_kw):
            pass

        def acquire(self):
            return False

    import tray.single_instance as si

    monkeypatch.setattr(si, "SingleInstance", Busy)
    monkeypatch.setattr(tray_app, "setup_logging", lambda paths: None)  # keep the real tray.log clean
    calls = []
    monkeypatch.setattr(si, "signal_show", lambda: calls.append("show") or True)
    boxes = []
    monkeypatch.setattr(tray_app.ui, "message_box", lambda *a, **k: boxes.append(a))
    assert tray_app.main([]) == 0
    assert calls == ["show"] and boxes == []
    # Autostart (--hidden) never pops anything when Polaris already runs.
    assert tray_app.main(["--hidden"]) == 0
    assert calls == ["show"] and boxes == []


def test_autostart_entry_starts_hidden():
    assert tray_app.HIDDEN_FLAG == "--hidden"


def test_microphone_only_for_own_app_page():
    assert dw.is_own_app_uri("http://127.0.0.1:8766/app/")
    assert dw.is_own_app_uri("http://localhost:8765/app/src/panel/index.html")
    assert not dw.is_own_app_uri("https://127.0.0.1:8766/app/")
    assert not dw.is_own_app_uri("http://evil.example/app/")
    assert not dw.is_own_app_uri("http://127.0.0.1:8766/other")
