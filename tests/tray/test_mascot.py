"""tray/mascot.py: settings persistence, geometry and the window wrapper (mock webview)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tray import mascot as m


WORK = m.Box(0, 0, 1536, 824)


# -- settings -----------------------------------------------------------------
def test_settings_defaults_and_persistence(tmp_path):
    path = tmp_path / "data" / "tray-settings.json"
    store = m.MascotSettings(path)
    assert store.enabled is True and store.anchor() is None

    store.set_enabled(False)
    store.save_anchor(900, 700)
    again = m.MascotSettings(path)
    assert again.enabled is False
    assert again.anchor() == (900, 700)


def test_settings_keep_other_keys_and_survive_garbage(tmp_path):
    path = tmp_path / "tray-settings.json"
    path.write_text(json.dumps({"window": {"x": 1, "y": 2, "width": 3, "height": 4}}), encoding="utf-8")
    store = m.MascotSettings(path)
    store.save_anchor(10, 20)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["window"] == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert data["mascot"] == {"right": 10, "bottom": 20}

    path.write_text("not json", encoding="utf-8")
    assert store.enabled is True and store.anchor() is None
    path.write_text(json.dumps({"mascot": {"enabled": "yes", "right": "x"}}), encoding="utf-8")
    assert store.enabled is True and store.anchor() is None


# -- geometry -----------------------------------------------------------------
def test_default_geometry_is_bottom_right_above_taskbar():
    geo = m.geometry_for(None, False, WORK)
    assert (geo.width, geo.height) == m.COLLAPSED
    assert geo.anchor == m.default_anchor(WORK)
    assert geo.x + geo.width <= WORK.right and geo.y + geo.height <= WORK.bottom


def test_expanding_keeps_the_star_corner():
    anchor = (900, 700)
    small = m.geometry_for(anchor, False, WORK)
    big = m.geometry_for(anchor, True, WORK)
    assert small.anchor == big.anchor == anchor
    assert (big.width, big.height) == m.EXPANDED


def test_off_screen_anchor_falls_back_and_expanded_is_clamped():
    assert m.geometry_for((5000, 5000), False, WORK).anchor == m.default_anchor(WORK)
    # Star near the top-left: the big bubble window is pushed back on screen.
    big = m.geometry_for((150, 150), True, WORK)
    assert big.x >= 0 and big.y >= 0
    assert m.anchor_on_screen((150, 150), WORK)
    assert not m.anchor_on_screen((50, 50), WORK)


def test_mascot_url():
    assert m.mascot_url("http://127.0.0.1:8766/") == "http://127.0.0.1:8766/app/src/panel/mascot.html"


# -- window wrapper (mock webview) -----------------------------------------------
class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class FakeWindow:
    def __init__(self, title, **kwargs):
        self.title = title
        self.kwargs = kwargs
        self.events = SimpleNamespace(closing=FakeEvent(), moved=FakeEvent(), loaded=FakeEvent(),
                                      resized=FakeEvent())
        self.calls = []
        self.x, self.y = kwargs["x"], kwargs["y"]
        self.width, self.height = kwargs["width"], kwargs["height"]
        self.native = None

    def load_url(self, url):
        self.calls.append(("load_url", url))

    def show(self):
        self.calls.append(("show",))

    def hide(self):
        self.calls.append(("hide",))

    def resize(self, width, height, fix=None):
        self.calls.append(("resize", width, height))
        self.x += self.width - width
        self.y += self.height - height
        self.width, self.height = width, height

    def destroy(self):
        self.calls.append(("destroy",))


class FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, **kwargs):
        window = FakeWindow(title, **kwargs)
        self.windows.append(window)
        return window


@pytest.fixture
def setup(tmp_path):
    wv = FakeWebview()
    opened = []
    changed = []
    store = m.MascotSettings(tmp_path / "tray-settings.json")
    mascot = m.MascotWindow(store, on_open_main=lambda: opened.append(1), webview_module=wv,
                            on_change=lambda: changed.append(1), work=lambda: WORK, screen=lambda: None)
    mascot.create(None)
    return SimpleNamespace(wv=wv, mascot=mascot, store=store, opened=opened, changed=changed,
                           window=wv.windows[0])


def test_create_is_hidden_frameless_on_top_with_key_colour(setup):
    kw = setup.window.kwargs
    assert setup.window.title == m.TITLE
    assert kw["hidden"] and kw["frameless"] and kw["on_top"] and not kw["easy_drag"]
    assert kw["background_color"] == m.KEY_COLOR
    assert (kw["width"], kw["height"]) == m.COLLAPSED
    api = kw["js_api"]
    assert [n for n in dir(api) if not n.startswith("_")] == ["hide_mascot", "open_main", "set_expanded", "set_hit_regions"]


def test_backend_url_waits_for_gui_then_loads_and_shows(setup):
    mascot, window = setup.mascot, setup.window
    mascot.set_backend("http://127.0.0.1:8765")
    assert window.calls == []
    mascot.mark_started()
    # show() re-applies the intended size (pywebview shrinks frameless forms).
    assert window.calls == [("load_url", "http://127.0.0.1:8765/app/src/panel/mascot.html"), ("show",),
                            ("resize", *m.COLLAPSED)]
    mascot.set_backend("http://127.0.0.1:8765")  # same url: nothing new
    assert len(window.calls) == 3


def test_disabled_mascot_stays_hidden(setup):
    setup.store.set_enabled(False)
    setup.mascot.mark_started()
    setup.mascot.set_backend("http://127.0.0.1:8765")
    assert ("show",) not in setup.window.calls


def test_js_api_expand_open_main_and_hide(setup):
    mascot, window = setup.mascot, setup.window
    mascot.mark_started()
    mascot.set_backend("http://127.0.0.1:8765")
    api = window.kwargs["js_api"]
    corner = (window.x + window.width, window.y + window.height)
    assert api.set_expanded(True)
    assert ("resize", *m.EXPANDED) in window.calls
    assert (window.x + window.width, window.y + window.height) == corner
    api.set_expanded(True)  # no duplicate resize
    assert sum(1 for c in window.calls if c[0] == "resize") == 2  # show + expand
    api.set_expanded(False)
    assert window.calls[-1] == ("resize", *m.COLLAPSED)

    assert api.open_main() is True and setup.opened == [1]

    api.hide_mascot()
    assert window.calls[-1] == ("hide",)
    assert setup.store.enabled is False and setup.changed == [1]


def test_toggle_persists_and_module_helpers(tmp_path):
    wv = FakeWebview()
    mascot = m.create_mascot_window(None, tmp_path / "s.json", lambda: None, webview_module=wv)
    assert m.current_mascot() is mascot
    mascot.mark_started()
    mascot.set_backend("http://127.0.0.1:8765")
    assert m.toggle_mascot() is False
    assert m.MascotSettings(tmp_path / "s.json").enabled is False
    assert wv.windows[0].calls[-1] == ("hide",)
    assert m.toggle_mascot() is True
    assert wv.windows[0].calls[-2:] == [("show",), ("resize", *m.COLLAPSED)]


def test_position_saved_after_move_and_close_only_hides(setup):
    mascot, window = setup.mascot, setup.window
    mascot.mark_started()
    mascot.set_backend("http://127.0.0.1:8765")
    window.x, window.y = 400, 300
    mascot._save_now()
    assert setup.store.anchor() == (400 + m.COLLAPSED[0], 300 + m.COLLAPSED[1])
    assert mascot._on_closing() is False
    mascot.destroy()
    assert mascot._on_closing() is True
    assert window.calls[-1] == ("destroy",)


# -- click-through mask ----------------------------------------------------------
def test_hit_shapes_scale_validate_and_clip():
    regions = [
        {"kind": "circle", "x": 40, "y": 30, "w": 40, "h": 40},
        {"kind": "rect", "x": 10, "y": 10, "w": 500, "h": 50},  # clipped to the client
        {"kind": "triangle", "x": 0, "y": 0, "w": 1, "h": 1},  # unknown kind
        {"kind": "rect", "x": "a", "y": 0, "w": 1, "h": 1},  # not a number
        {"kind": "rect", "x": float("nan"), "y": 0, "w": 1, "h": 1},
        {"kind": "rect", "x": 0, "y": 0, "w": 0, "h": 5},  # empty
        "junk",
    ]
    shapes = m.hit_shapes(regions, 1.25, (188, 188))
    assert shapes == [m.HitShape("circle", 50, 38, 50, 50), m.HitShape("rect", 12, 12, 176, 62)]
    assert m.hit_shapes("nope", 1.0) == []
    assert len(m.hit_shapes([{"kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}] * 50, 1.0)) == m.MAX_HIT_REGIONS


def test_set_hit_regions_without_gui_is_harmless(setup):
    api = setup.window.kwargs["js_api"]
    assert api.set_hit_regions([{"kind": "circle", "x": 1, "y": 1, "w": 2, "h": 2}]) is False
    assert setup.mascot._hit_regions[0]["kind"] == "circle"
