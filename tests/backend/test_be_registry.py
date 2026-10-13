import pytest

from backend.tools import registry as reg
from backend.tools.registry import Tool


async def _h(args, ctx):
    return {"ok": True}


def mk(name, risk="safe", params=None):
    return Tool(
        name=name,
        description="d",
        parameters=params or {"type": "object", "properties": {"ref": {"type": "integer"}}, "required": ["ref"]},
        risk=risk,
        handler=_h,
    )


@pytest.fixture(autouse=True)
def isolated_registry():
    saved = dict(reg._REGISTRY)
    reg.clear()
    yield
    reg.clear()
    reg._REGISTRY.update(saved)


def test_register_get_and_dup_replaces():
    t1, t2 = mk("browser.click"), mk("browser.click")
    reg.register(t1)
    assert reg.get("browser.click") is t1
    reg.register(t2)
    assert reg.get("browser.click") is t2 and len(reg.all_tools()) == 1


def test_get_accepts_llm_name_and_unknown():
    t = mk("browser.click")
    reg.register(t)
    assert reg.get("browser__click") is t
    assert reg.get("nope.x") is None and reg.get("plain") is None


def test_tool_validation():
    with pytest.raises(ValueError):
        mk("noprefix")
    with pytest.raises(ValueError):
        mk("a.b", params={"type": "array"})


def test_name_mapping_roundtrip():
    assert reg.llm_name("browser.click") == "browser__click"
    assert reg.from_llm_name("browser__click") == "browser.click"
    assert reg.from_llm_name("browser.click") == "browser.click"
    assert reg.from_llm_name("finish") == "finish"
    assert reg.from_llm_name(reg.llm_name("web.search")) == "web.search"


def test_list_for_hides_web_search_without_brave_key(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    for n in ("browser.snapshot", "web.search"):
        reg.register(mk(n))
    assert {t.name for t in reg.list_for("browser_task")} == {"browser.snapshot"}


def test_list_for_routes(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test")
    for n in ("browser.snapshot", "browser.click", "web.search", "memory.save"):
        reg.register(mk(n))
    assert reg.list_for("chat") == []
    assert {t.name for t in reg.list_for("browser_task")} == {"browser.snapshot", "browser.click", "web.search"}
    assert [t.name for t in reg.list_for("personal")] == ["memory.save"]
    assert reg.list_for("unknown_route") == []
    assert reg.list_for("desktop_task") == []


def test_schema_and_inject_why_does_not_mutate():
    t = mk("browser.click")
    s = reg.to_llm_schema(t)
    assert s["type"] == "function" and s["function"]["name"] == "browser__click"
    assert "why" not in s["function"]["parameters"]["properties"]
    w = reg.to_llm_schema_with_why(t)
    params = w["function"]["parameters"]
    assert "why" in params["properties"] and params["required"] == ["ref", "why"]
    assert "why" not in t.parameters["properties"] and t.parameters["required"] == ["ref"]
    assert reg.inject_why({"type": "object"})["required"] == ["why"]
    assert len(reg.to_llm_schemas([t, mk("a.b")])) == 2


def test_inject_why_idempotent_required():
    p = reg.inject_why(reg.inject_why({"type": "object", "properties": {}}))
    assert p["required"].count("why") == 1


@pytest.mark.parametrize("label,risky", [
    ("Siparişi Onayla", True), ("Gönder", True), ("Buy now", True), ("DELETE", True), ("Satın al", True),
    ("Ara", False), ("Giriş", False), ("", False), (None, False),
])
def test_is_risky_label(label, risky):
    assert reg.is_risky_label(label) is risky


SNAP = {"elements": [{"ref": 1, "name": "Ara"}, {"ref": 2, "name": "Siparişi onayla"}, {"ref": 3, "value": "Send"}]}


def test_find_element():
    assert reg.find_element(SNAP, 2)["name"] == "Siparişi onayla"
    assert reg.find_element(SNAP, "2")["ref"] == 2
    assert reg.find_element(SNAP, 99) is None
    assert reg.find_element(None, 1) is None
    assert reg.find_element({"elements": "x"}, 1) is None
    assert reg.find_element(SNAP, "abc") is None


def test_needs_confirm_gate():
    click = mk("browser.click")
    typ = mk("browser.type")
    assert reg.needs_confirm(click, {"ref": 1}, SNAP) is None
    assert reg.needs_confirm(click, {"ref": 2}, SNAP)
    assert reg.needs_confirm(click, {"ref": 3}, SNAP)  # label from value
    assert reg.needs_confirm(click, {"ref": 99}, SNAP)  # unknown ref -> ask
    assert reg.needs_confirm(click, {"ref": 1}, None)  # no snapshot -> ask
    assert reg.needs_confirm(mk("desktop.click"), {"ref": 2}, SNAP)
    assert reg.needs_confirm(typ, {"ref": 1, "text": "x"}, SNAP) is None
    assert reg.needs_confirm(typ, {"submit": True}, SNAP)
    assert reg.needs_confirm(typ, {"submit": "true"}, SNAP)
    assert reg.needs_confirm(typ, {"submit": "false"}, SNAP) is None
    assert reg.needs_confirm(typ, {"submit": 0}, SNAP) is None
    assert reg.needs_confirm(mk("reminders.add", risk="confirm"), {}, None)
    assert reg.needs_confirm(mk("browser.read"), {}, None) is None
    assert reg.click_needs_confirm(click, {"ref": 2}, SNAP)


def test_is_truthy():
    assert reg.is_truthy(True) and reg.is_truthy(1) and reg.is_truthy(" Evet ")
    assert not (reg.is_truthy(None) or reg.is_truthy("no") or reg.is_truthy(0) or reg.is_truthy([]))
