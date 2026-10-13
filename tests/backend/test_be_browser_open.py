"""`browser.open` registration, routing and handler behaviour."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.tools import browser as browser_tools
from backend.tools import registry as reg


class FakeBridge:
    def __init__(self, reply: Any = None, exc: Exception | None = None) -> None:
        self.reply = reply
        self.exc = exc
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> Any:
        self.calls.append((name, dict(args)))
        if self.exc is not None:
            raise self.exc
        return self.reply


def ctx(bridge: FakeBridge) -> reg.ToolContext:
    async def emit(_step: Any) -> None:
        return None

    return reg.ToolContext(request_id="r_t", bridge=bridge, tab=None, emit_trace=emit)  # type: ignore[arg-type]


@pytest.fixture
def open_tool() -> reg.Tool:
    browser_tools.register_browser_tools()
    tool = reg.get("browser.open")
    assert tool is not None
    return tool


def test_open_is_registered_safe_with_url_schema(open_tool: reg.Tool) -> None:
    assert open_tool.risk == "safe"
    props = open_tool.parameters["properties"]
    assert set(props) == {"url", "new_tab"}
    assert open_tool.parameters["required"] == ["url"]
    assert props["new_tab"]["type"] == "boolean"
    assert reg.get("browser__open") is open_tool
    assert reg.needs_confirm(open_tool, {"url": "https://example.com"}, None) is None
    names = {t.name for t in browser_tools.register_browser_tools()}
    assert len([n for n in names if n.startswith("browser.")]) == 8


def test_open_is_exposed_for_browser_and_web_search_routes() -> None:
    assert "browser.open" in reg.ROUTE_TOOLS["browser_task"]
    assert "browser.open" in reg.ROUTE_TOOLS["web_search"]
    assert "browser.open" not in reg.ROUTE_TOOLS["chat"]
    browser_tools.register_browser_tools()
    assert "browser.open" in {t.name for t in reg.list_for("browser_task")}


def test_open_forwards_to_bridge_with_new_tab_default(open_tool: reg.Tool) -> None:
    bridge = FakeBridge({"ok": True, "data": {"ok": True, "url": "https://tr.wikipedia.org/", "title": "Vikipedi", "new_tab": True, "tab_id": 5}})
    result = asyncio.run(open_tool.handler({"url": " https://tr.wikipedia.org "}, ctx(bridge)))
    assert bridge.calls == [("browser.open", {"url": "https://tr.wikipedia.org", "new_tab": True})]
    assert result == {"ok": True, "data": {"url": "https://tr.wikipedia.org/", "title": "Vikipedi", "new_tab": True}}


def test_open_new_tab_false_and_string_flags(open_tool: reg.Tool) -> None:
    bridge = FakeBridge({"ok": True, "data": {"url": "https://a.example/", "title": "A", "new_tab": False}})
    asyncio.run(open_tool.handler({"url": "https://a.example/", "new_tab": "false"}, ctx(bridge)))
    assert bridge.calls[-1][1]["new_tab"] is False


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "file:///C:/x", "chrome://settings", "", None])
def test_open_rejects_non_http_urls_without_bridge(open_tool: reg.Tool, url: Any) -> None:
    bridge = FakeBridge({"ok": True})
    result = asyncio.run(open_tool.handler({"url": url}, ctx(bridge)))
    assert result["ok"] is False and result["error"]
    assert bridge.calls == []


def test_open_allows_bare_host_and_host_port(open_tool: reg.Tool) -> None:
    bridge = FakeBridge({"ok": True, "data": {}})
    for url in ("tr.wikipedia.org", "localhost:3000/x"):
        result = asyncio.run(open_tool.handler({"url": url}, ctx(bridge)))
        assert result["ok"] is True
    assert [c[1]["url"] for c in bridge.calls] == ["tr.wikipedia.org", "localhost:3000/x"]


def test_open_bridge_failure_is_a_tool_error_not_an_exception(open_tool: reg.Tool) -> None:
    result = asyncio.run(open_tool.handler({"url": "https://x.example"}, ctx(FakeBridge(exc=TimeoutError("yok")))))
    assert result["ok"] is False and "köprüsü" in result["error"]
    result = asyncio.run(open_tool.handler({"url": "https://x.example"}, ctx(FakeBridge({"ok": False, "error": "Sekme açılamadı"}))))
    assert result == {"ok": False, "error": "Sekme açılamadı"}


def test_open_args_validate_against_schema(open_tool: reg.Tool) -> None:
    assert reg.validate_args(open_tool, {"url": "https://x", "why": "neden"}) is None
    assert reg.validate_args(open_tool, {"new_tab": True}) is not None
    assert "why" in reg.to_llm_schema_with_why(open_tool)["function"]["parameters"]["required"]
