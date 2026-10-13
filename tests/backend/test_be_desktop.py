"""Polaris desktop window: /app static route and `hello.client == "desktop"` handling."""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import orchestrator as orch
from backend.agents.base import BROWSER_AVAILABLE, NO_BROWSER_NOTE, ExecutorAction, FinalAnswer, Plan, build_context_block
from backend.desktop_app import panel_dist, register, rewrite_index
from backend.tools import registry
from backend.tools.registry import Tool
from backend.trace import TraceStore

INDEX = """<!doctype html><html><head>
<script type="module" crossorigin src="/assets/index.html-X.js"></script>
<link rel="stylesheet" crossorigin href="/assets/styles-Y.css">
</head><body><div id="root"></div></body></html>"""


# -- /app static route -------------------------------------------------------------
@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "src" / "panel").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "src" / "panel" / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "index.html-X.js").write_text("export const x = 1;", encoding="utf-8")
    (root / "assets" / "styles-Y.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return root


@pytest.fixture
def client(dist):
    app = FastAPI()
    register(app, dist=dist)
    return TestClient(app)


def test_rewrite_index_points_assets_at_app():
    html = rewrite_index(INDEX)
    assert 'src="/app/assets/index.html-X.js"' in html
    assert 'href="/app/assets/styles-Y.css"' in html
    assert '"/assets/' not in html


def test_app_redirects_and_serves_index(client):
    r = client.get("/app", follow_redirects=False)
    assert r.status_code in (307, 308) and r.headers["location"] == "/app/"
    r = client.get("/app/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "/app/assets/index.html-X.js" in r.text
    assert client.get("/app/src/panel/index.html").text == r.text


def test_app_serves_assets_with_js_mime(client):
    r = client.get("/app/assets/index.html-X.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert client.get("/app/assets/styles-Y.css").headers["content-type"].startswith("text/css")


def test_app_blocks_traversal_and_missing(client):
    assert client.get("/app/../secret.txt").status_code == 404
    assert client.get("/app/%2e%2e/secret.txt").status_code == 404
    assert client.get("/app/assets/nope.js").status_code == 404


def test_app_not_built_gives_turkish_404(tmp_path):
    app = FastAPI()
    register(app, dist=tmp_path / "missing")
    r = TestClient(app).get("/app/")
    assert r.status_code == 404 and "derlenmemiş" in r.json()["detail"]


def test_panel_dist_prefers_release_layout(tmp_path):
    assert panel_dist(tmp_path) == tmp_path / "extension" / "dist"
    (tmp_path / "extension-dist" / "src" / "panel").mkdir(parents=True)
    (tmp_path / "extension-dist" / "src" / "panel" / "index.html").write_text("x")
    assert panel_dist(tmp_path) == tmp_path / "extension-dist"


def test_main_app_has_app_route():
    from backend.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/app/" in paths and "/app/{path:path}" in paths


# -- desktop hello -------------------------------------------------------------------
class Router:
    def __init__(self, route):
        self.route_name = route

    async def route(self, **_kw):
        return SimpleNamespace(route=self.route_name, confidence=0.9, why="w")


class Planner:
    async def plan(self, **_kw):
        return Plan(goal="hedef")


class RecordingExecutor:
    def __init__(self, actions):
        self.actions = list(actions)
        self.tools_seen = []
        self.notes = []

    async def step(self, **kw):
        self.tools_seen.append([t.name for t in kw["tools"]])
        self.notes.append(build_context_block())
        return self.actions.pop(0) if self.actions else ExecutorAction(kind="finish", summary="bitti")


class Judge:
    async def judge(self, **_kw):
        return SimpleNamespace(verdict="pass", feedback="")


class Responder:
    def __init__(self):
        self.calls = []
        self.contexts = []

    async def respond(self, **kw):
        self.calls.append(kw)
        self.contexts.append(build_context_block())
        return FinalAnswer(answer="tamam", spoken="tamam")


async def _ok_handler(_args, _ctx):
    return {"ok": True, "data": {"x": 1}, "error": None}


@pytest.fixture
def fake_tools():
    saved = dict(registry._REGISTRY)
    for name in ("browser.read", "browser.open", "browser.navigate", "web.search"):
        registry.register(Tool(name=name, description="d", parameters={"type": "object", "properties": {}},
                               risk="safe", handler=_ok_handler))
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def _orch(tmp_path, route, executor=None):
    sent = []

    async def send(frame):
        sent.append(frame)

    o = orch.Orchestrator(send, store=TraceStore(str(tmp_path / "d.db")), client=SimpleNamespace(configured=True))
    o._settings = dataclasses.replace(o._settings, request_budget_s=30.0)
    o.router, o.planner, o.judge, o.responder = Router(route), Planner(), Judge(), Responder()
    o.executor = executor or RecordingExecutor([])
    return o, sent


async def test_hello_sets_client_kind(tmp_path):
    o, _ = _orch(tmp_path, "chat")
    assert o.browser_available
    await o.handle_frame({"type": "hello", "client": "desktop", "version": "0.1"})
    assert o.client_kind == "desktop" and not o.browser_available
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    assert o.browser_available
    await o.close()


async def test_desktop_excludes_browser_tools(tmp_path, fake_tools):
    o, _ = _orch(tmp_path, "web_search")
    await o.handle_frame({"type": "hello", "client": "desktop"})
    await asyncio.wait_for(o._run_request("r_d1", "hava nasıl", "text", None), timeout=10)
    await o.close()
    seen = o.executor.tools_seen[0]
    assert "web.search" in seen
    assert not any(name.startswith("browser.") for name in seen)
    assert NO_BROWSER_NOTE in o.executor.notes[0]


async def test_extension_keeps_browser_tools(tmp_path, fake_tools):
    o, _ = _orch(tmp_path, "web_search")
    await o.handle_frame({"type": "hello", "client": "extension"})
    await asyncio.wait_for(o._run_request("r_e1", "hava nasıl", "text", None), timeout=10)
    await o.close()
    assert "browser.read" in o.executor.tools_seen[0]
    assert NO_BROWSER_NOTE not in o.executor.notes[0]


async def test_desktop_browser_task_becomes_chat(tmp_path, fake_tools):
    o, sent = _orch(tmp_path, "browser_task")
    await o.handle_frame({"type": "hello", "client": "desktop"})
    await asyncio.wait_for(o._run_request("r_d2", "YouTube'u aç", "text", None), timeout=10)
    await o.close()
    assert o.executor.tools_seen == []  # no tool loop at all
    assert o.responder.calls[0]["route"] == "chat"
    assert NO_BROWSER_NOTE in o.responder.contexts[0]
    assert any(f["type"] == "final" for f in sent)
    assert not any(f["type"] == "tool_call" for f in sent)
    assert any("Tarayıcı erişimi yok" in f["step"]["decision"] for f in sent if f["type"] == "trace")


async def test_desktop_refuses_named_browser_tool_without_bridge(tmp_path, fake_tools):
    executor = RecordingExecutor([ExecutorAction(kind="tool", tool="browser.open", args={})])
    o, sent = _orch(tmp_path, "web_search", executor)
    await o.handle_frame({"type": "hello", "client": "desktop"})
    history_seen = []
    original = executor.step

    async def step(**kw):
        history_seen.append(list(kw["history"]))
        return await original(**kw)

    executor.step = step
    await asyncio.wait_for(o._run_request("r_d3", "aç", "text", None), timeout=10)
    await o.close()
    assert not any(f["type"] == "tool_call" for f in sent)
    entry = history_seen[-1][-1]
    assert entry.tool == "browser.open" and not entry.ok and entry.error == orch.NO_BROWSER_ERROR


def test_context_block_default_has_no_desktop_note():
    assert BROWSER_AVAILABLE.get() is True
    assert NO_BROWSER_NOTE not in build_context_block()


def test_app_serves_other_built_pages_rewritten(tmp_path):
    """The desktop mascot page (mascot.html) gets the same /app/assets rewrite."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.desktop_app import register

    page = tmp_path / "src" / "panel" / "mascot.html"
    page.parent.mkdir(parents=True)
    page.write_text('<script type="module" src="/assets/mascot-Z.js"></script>', encoding="utf-8")
    app = FastAPI()
    register(app, dist=tmp_path)
    r = TestClient(app).get("/app/src/panel/mascot.html")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'src="/app/assets/mascot-Z.js"' in r.text
