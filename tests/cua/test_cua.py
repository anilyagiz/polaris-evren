"""CUA layer tests: run entirely on FakeDesktopSession (no Docker, no OCR)."""
from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cua import annotate as annotate_mod
from backend.cua import session as session_mod
from backend.cua import shots_router as router_mod
from backend.cua.perceive import build_snapshot, format_for_llm, merge_lines, to_elements
from backend.tools.registry import ToolContext


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(annotate_mod, "SHOTS_DIR", tmp_path)
    monkeypatch.setattr(router_mod, "SHOTS_DIR", tmp_path)
    monkeypatch.setenv("CUA_ENABLED", "true")
    monkeypatch.setenv("DESKTOP_FAKE", "1")
    fake = session_mod.FakeDesktopSession()
    session_mod.set_session(fake)
    from backend.tools import desktop

    desktop._elements.clear()
    desktop._shot_counter.clear()
    yield fake, desktop, tmp_path
    session_mod.set_session(None)


def make_ctx(traces):
    async def emit(step):
        traces.append(step)

    return ToolContext(request_id="r_test", bridge=None, tab=None, emit_trace=emit)


def run(coro):
    return asyncio.run(coro)


def tool(desktop, name):
    return next(t for t in desktop.TOOLS if t.name == name)


def test_fake_env_selects_fake(monkeypatch):
    session_mod.set_session(None)
    monkeypatch.setenv("DESKTOP_FAKE", "1")
    assert isinstance(session_mod.get_session(), session_mod.FakeDesktopSession)
    session_mod.set_session(None)


def test_build_snapshot_format(env):
    fake, _, tmp = env
    png = run(fake.screenshot())
    snap = build_snapshot(png, fake.known_elements(), "r_x", 3)
    assert snap["source"] == "desktop" and snap["shot"] == "/shots/r_x_3.png"
    assert (tmp / "r_x_3.png").is_file()
    assert 0 < len(snap["elements"]) <= 150
    for i, el in enumerate(snap["elements"], 1):
        assert el["ref"] == i and el["role"] and len(el["name"]) <= 80 and len(el["bbox"]) == 4
    assert "Kaydet" in format_for_llm(snap)


def test_element_cap_and_name_trim():
    boxes = [{"text": "x" * 200, "bbox": [0, i * 20, 50, 10]} for i in range(300)]
    els = to_elements(merge_lines(boxes))
    assert len(els) == 150 and len(els[0]["name"]) == 80


def test_snapshot_tool_emits_trace(env):
    fake, desktop, _ = env
    traces: list = []
    res = run(tool(desktop, "desktop.snapshot").handler({}, make_ctx(traces)))
    assert res["ok"] and res["data"]["shot"] == "/shots/r_test_1.png"
    assert traces and traces[0].evidence == ["shot:/shots/r_test_1.png"]
    res2 = run(tool(desktop, "desktop.snapshot").handler({}, make_ctx(traces)))
    assert res2["data"]["shot"] == "/shots/r_test_2.png"


def test_click_unknown_ref_exact_error(env):
    _, desktop, _ = env
    res = run(tool(desktop, "desktop.click").handler({"ref": 5}, make_ctx([])))
    assert res == {"ok": False, "error": "ref bulunamadı, önce desktop.snapshot çağır"}
    res = run(tool(desktop, "desktop.click").handler({"ref": "abc"}, make_ctx([])))
    assert res["error"] == "ref bulunamadı, önce desktop.snapshot çağır"


def test_full_flow_click_type_key_scroll(env, monkeypatch):
    fake, desktop, _ = env
    monkeypatch.setattr(desktop, "CLICK_SETTLE_S", 0)
    ctx = make_ctx([])
    snap = run(tool(desktop, "desktop.snapshot").handler({}, ctx))["data"]
    ref = next(e["ref"] for e in snap["elements"] if e["name"] == "Kaydet")
    el = next(e for e in snap["elements"] if e["ref"] == ref)
    assert run(tool(desktop, "desktop.click").handler({"ref": ref}, ctx)) == {"ok": True}
    x, y, w, h = el["bbox"]
    assert fake.calls[-1] == ("click", x + w // 2, y + h // 2)
    assert fake.status == "Kaydedildi"
    # focus editor text and type
    line = next(e for e in snap["elements"] if e["name"].startswith("Merhaba"))
    run(tool(desktop, "desktop.click").handler({"ref": line["ref"]}, ctx))
    assert run(tool(desktop, "desktop.type").handler({"text": " Selam"}, ctx)) == {"ok": True}
    assert fake.text.endswith(" Selam")
    assert run(tool(desktop, "desktop.key").handler({"combo": "ctrl+s"}, ctx)) == {"ok": True}
    assert run(tool(desktop, "desktop.scroll").handler({"direction": "down"}, ctx)) == {"ok": True}
    assert not run(tool(desktop, "desktop.scroll").handler({"direction": "left"}, ctx))["ok"]
    assert not run(tool(desktop, "desktop.type").handler({"text": ""}, ctx))["ok"]
    assert not run(tool(desktop, "desktop.key").handler({"combo": ""}, ctx))["ok"]


def test_tool_names_and_args(env):
    _, desktop, _ = env
    spec = {t.name: t.parameters.get("required", []) for t in desktop.TOOLS}
    assert spec == {
        "desktop.snapshot": [],
        "desktop.click": ["ref"],
        "desktop.type": ["text"],
        "desktop.key": ["combo"],
        "desktop.scroll": ["direction"],
    }


def test_registration_noop_when_disabled(monkeypatch):
    from backend.tools import desktop

    monkeypatch.setenv("CUA_ENABLED", "false")
    assert desktop.register_desktop_tools() is False


def test_shots_router(env):
    fake, _, tmp = env
    snap = build_snapshot(run(fake.screenshot()), fake.known_elements(), "r_a", 1)
    app = FastAPI()
    app.include_router(router_mod.router)
    c = TestClient(app)
    r = c.get(snap["shot"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/shots/nope.png").status_code == 404
    assert c.get("/shots/r_a_1.jpg").status_code == 404
    for bad in ("..%2f..%2fsecret.png", "%2e%2e%2fx.png", "..%5cx.png", "a..b.png", "..png", "r_a_1.png%00"):
        assert c.get(f"/shots/{bad}").status_code == 404, bad
