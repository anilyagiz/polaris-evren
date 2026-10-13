"""P3: trace masking, argument validation, /health, /traces guard, rate limit, logging, banner."""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from backend.tools import registry as reg
from backend.trace import (
    MASK,
    MAX_HISTORY_LIMIT,
    TraceStore,
    cap_strings,
    make_emitter,
    make_step,
    redact_tool_args,
)


async def _noop(*_a, **_k):
    return {"ok": True}


def _tool(name: str, params: dict) -> reg.Tool:
    return reg.Tool(name=name, description="d", parameters=params, risk="safe", handler=_noop)


TYPE_TOOL = _tool(
    "browser.type",
    {
        "type": "object",
        "properties": {
            "ref": {"type": "integer"},
            "text": {"type": "string"},
            "submit": {"type": "boolean"},
        },
        "required": ["ref", "text"],
    },
)
SCROLL_TOOL = _tool(
    "browser.scroll",
    {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]}}, "required": ["direction"]},
)
MEM_TOOL = _tool(
    "memory.save",
    {"type": "object", "properties": {"text": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]},
)

SNAP = {
    "elements": [
        {"ref": 1, "role": "textbox", "name": "Ad Soyad"},
        {"ref": 2, "role": "textbox", "name": "Kart Numarası"},
        {"ref": 3, "role": "textbox", "name": "ŞİFRE"},
        {"ref": 4, "role": "textbox", "name": "Güvenlik kodu", "autocomplete": "cc-csc"},
        {"ref": 5, "role": "textbox", "name": "CVV"},
    ]
}


# -- masking -----------------------------------------------------------------
@pytest.mark.parametrize("ref", [2, 3, 4, 5])
def test_type_into_sensitive_field_is_masked(ref):
    out = redact_tool_args("browser.type", {"ref": ref, "text": "4111 1111"}, SNAP)
    assert out["text"] == MASK and out["ref"] == ref


def test_type_into_normal_field_kept_and_args_not_mutated():
    args = {"ref": 1, "text": "Ali Veli"}
    assert redact_tool_args("browser.type", args, SNAP)["text"] == "Ali Veli"
    masked = redact_tool_args("browser.type", {"ref": 5, "text": "123"}, SNAP)
    assert masked is not args and args["text"] == "Ali Veli"
    assert redact_tool_args("desktop.type", {"text": "x"}, SNAP) == {"text": "x"}


def test_cap_strings():
    capped = cap_strings({"a": "x" * 500, "b": ["y" * 300, 3], "c": 1})
    assert len(capped["a"]) == 201 and capped["a"].endswith("…")
    assert len(capped["b"][0]) == 201 and capped["b"][1] == 3 and capped["c"] == 1


async def test_emitter_masks_sent_and_stored(tmp_path):
    db = str(tmp_path / "t.db")
    store = TraceStore(db)
    sent = []

    async def send(frame):
        sent.append(frame)

    emit = make_emitter(send, store, "r_mask", snapshot_provider=lambda: SNAP)
    step = make_step("executor", "Yaz", "neden", tool={"name": "browser.type", "args": {"ref": 5, "text": "999"}})
    returned = await emit(step)
    assert returned.tool.args["text"] == "999"  # caller's object untouched
    assert sent[0]["step"]["tool"]["args"]["text"] == MASK
    await emit(make_step("executor", "Yaz", "n", tool={"name": "browser.type", "args": {"ref": 1, "text": "z" * 900}}))
    assert len(sent[1]["step"]["tool"]["args"]["text"]) == 900  # sent unchanged (not sensitive)
    with sqlite3.connect(db) as conn:
        rows = [json.loads(r[0]) for r in conn.execute("SELECT tool_args FROM traces ORDER BY id")]
    assert rows[0]["text"] == MASK
    assert len(rows[1]["text"]) == 201  # stored copy capped


async def test_history_limit_capped(tmp_path):
    store = TraceStore(str(tmp_path / "h.db"))
    await store.init()
    assert await store.history("none", limit=10**9) == []
    assert MAX_HISTORY_LIMIT <= 1000


# -- argument validation -----------------------------------------------------
def test_validate_ok_and_lenient_types():
    assert reg.validate_args(TYPE_TOOL, {"ref": 3, "text": "a", "why": "x"}) is None
    assert reg.validate_args(TYPE_TOOL, {"ref": "3", "text": "a", "submit": "true", "extra": 1}) is None
    assert reg.validate_args(SCROLL_TOOL, {"direction": "down"}) is None
    assert reg.validate_args(MEM_TOOL, {"text": "a", "tags": ["x"]}) is None


def test_validate_errors_are_turkish():
    err = reg.validate_args(TYPE_TOOL, {"text": "a"})
    assert err and "`ref` zorunlu" in err and "browser.type" in err
    assert "tam sayı" in reg.validate_args(TYPE_TOOL, {"ref": "on iki", "text": "a"})
    assert "true/false" in reg.validate_args(TYPE_TOOL, {"ref": 1, "text": "a", "submit": "belki"})
    assert "şunlardan biri" in reg.validate_args(SCROLL_TOOL, {"direction": "left"})
    assert "liste" in reg.validate_args(MEM_TOOL, {"text": "a", "tags": "x"})
    assert "öğeleri" in reg.validate_args(MEM_TOOL, {"text": "a", "tags": [1]})
    assert reg.validate_args(TYPE_TOOL, ["x"])
    assert reg.validate_args(TYPE_TOOL, {"ref": True, "text": "a"})  # bool is not an integer


# -- HTTP surface ------------------------------------------------------------
@pytest.fixture
def app_module(tmp_path, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "store", TraceStore(str(tmp_path / "api.db")))
    main.voice_limiter.reset()
    yield main
    main.voice_limiter.reset()


def _client(main, host="127.0.0.1"):
    from fastapi.testclient import TestClient

    return TestClient(main.app, client=(host, 50000))


def test_health_fields(app_module):
    data = _client(app_module).get("/health").json()
    for key in ("ok", "llm_model", "cua_enabled", "llm_configured"):
        assert key in data
    assert data["version"] == "0.2.0"
    assert isinstance(data["brave_configured"], bool)
    assert isinstance(data["stt_ready"], bool) and isinstance(data["tts_available"], bool)


def test_traces_loopback_only(app_module):
    assert _client(app_module, "127.0.0.1").get("/traces/r_x?limit=99999").status_code == 200
    assert _client(app_module, "::1").get("/traces/r_x").status_code == 200
    r = _client(app_module, "192.168.1.20").get("/traces/r_x")
    assert r.status_code == 403 and "localhost" in r.json()["detail"]


def test_voice_rate_limit(app_module, monkeypatch):
    monkeypatch.setattr(app_module.voice_limiter, "limit", 2)
    c = _client(app_module, "10.0.0.5")
    codes = [c.post("/tts", json={}).status_code for _ in range(3)]
    assert codes[:2] != [429, 429] and codes[2] == 429
    r = c.post("/tts", json={})
    assert r.status_code == 429 and "Çok fazla istek" in r.json()["detail"] and r.headers["Retry-After"]
    # other clients and other paths are counted separately
    assert _client(app_module, "10.0.0.6").post("/tts", json={}).status_code != 429
    assert c.get("/health").status_code == 200


def test_rate_limiter_window():
    from backend.main import RateLimiter

    lim = RateLimiter(2, 60)
    assert lim.hit("a", 0) is None and lim.hit("a", 1) is None
    assert lim.hit("a", 2) == pytest.approx(58)
    assert lim.hit("a", 61) is None


# -- config / banner / logging -------------------------------------------------
def test_config_warnings_never_leak_secrets(settings_env, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    from backend import config

    s = settings_env(DEEPSEEK_API_KEY="sk-supersecret")
    warnings = config.config_warnings(s)
    assert any("LLM_MODEL" in w for w in warnings) and any("BRAVE_API_KEY" in w for w in warnings)
    assert not any("DEEPSEEK_API_KEY" in w for w in warnings)
    assert all("supersecret" not in w for w in warnings)
    assert config.missing_keys(s) == ["LLM_MODEL", "BRAVE_API_KEY"]
    s2 = settings_env(DEEPSEEK_API_KEY="", LLM_MODEL="deepseek-chat", BRAVE_API_KEY="b-secret")
    assert config.missing_keys(s2) == ["DEEPSEEK_API_KEY"]
    assert s2.host == "127.0.0.1" and s2.port == 8765


def test_banner(settings_env, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    settings_env(DEEPSEEK_API_KEY="sk-hidden", LLM_MODEL="deepseek-chat")
    from backend.__main__ import banner

    text = banner("127.0.0.1", 8123)
    assert "http://localhost:8123" in text and "deepseek-chat" in text
    assert "BRAVE_API_KEY" in text and "sk-hidden" not in text
    assert "yerel değil" in banner("0.0.0.0", 8000)


def test_setup_logging_file_utf8(tmp_path):
    from backend import logging_setup

    root = logging.getLogger()
    before = list(root.handlers)
    root.handlers = []  # backend.main may already have attached the real file handler
    path = tmp_path / "logs" / "p.log"
    try:
        assert logging_setup.setup_logging(path, console=False) == path
        logging.getLogger("backend.test").warning("Şifre İğdır çalışıyor")
        for h in root.handlers:
            h.flush()
        assert "Şifre İğdır" in path.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        root.handlers = before


# -- orchestrator: invalid args never reach the handler ------------------------
async def test_orchestrator_rejects_invalid_args_before_handler(tmp_path, monkeypatch):
    import time

    from backend import orchestrator as orch
    from backend.agents.base import ExecutorAction, Plan

    calls = []

    async def handler(args, ctx):
        calls.append(args)
        return {"ok": True, "data": {"done": True}}

    tool = reg.Tool(
        name="ptest.echo",
        description="d",
        parameters={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        risk="safe",
        handler=handler,
    )
    monkeypatch.setitem(reg._REGISTRY, "ptest.echo", tool)

    actions = [
        ExecutorAction(kind="tool", tool="ptest.echo", args={"n": "abc"}, why="w"),
        ExecutorAction(kind="tool", tool="ptest.echo", args={"n": 2}, why="w"),
        ExecutorAction(kind="finish", summary="bitti"),
    ]

    class FakeExecutor:
        async def step(self, **_kw):
            return actions.pop(0)

    sent = []

    async def send(frame):
        sent.append(frame)

    o = orch.Orchestrator(send, store=TraceStore(str(tmp_path / "o.db")))
    emit = make_emitter(send, o.store, "r_val")
    history = []
    run = await o._run_tool_loop(
        request_id="r_val", executor=FakeExecutor(), text="t", tab=None, route="personal",
        plan=Plan(goal="g"), history=history, snapshot=None, calls_used=0,
        deadline=time.monotonic() + 30, emit=emit,
        log=logging.LoggerAdapter(logging.getLogger("t"), {}),
    )
    await o.close()
    assert calls == [{"n": 2}]
    assert history[0].ok is False and "tam sayı" in history[0].error
    assert history[1].ok is True and run.summary == "bitti"
    assert any(f["type"] == "trace" and "geçersiz" in f["step"]["decision"] for f in sent)
