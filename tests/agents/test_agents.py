"""Focused agent/LLM tests using fakes (no network)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import backend.agents  # noqa: F401  (openai compat shim)
from backend import llm as llm_mod
from backend.agents.base import FinalAnswer, Plan, PlanStep
from backend.agents.executor import ExecutorAgent
from backend.agents.judge import JudgeAgent
from backend.agents.planner import PlannerAgent
from backend.agents.responder import ResponderAgent
from backend.agents.router import RouterAgent
from backend.config import PROMPTS_DIR, get_settings
from backend.llm import LLMClient, LLMError, ToolCall, ToolMessage, Usage, _loads_lenient
from backend.tools import browser as _browser_tools  # noqa: F401  (registers browser.*)
from backend.tools import registry


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, json_reply: Any = None, tool_reply: ToolMessage | None = None, raises: bool = False):
        self.json_reply, self.tool_reply, self.raises = json_reply, tool_reply, raises
        self.systems: list[str] = []
        self.tools_seen: list[Any] = []

    async def chat_json(self, system, messages, temperature=0.0):
        self.systems.append(system)
        if self.raises:
            raise LLMError("boom")
        return self.json_reply, Usage(total_tokens=5)

    async def chat_tools(self, system, messages, tools, temperature=0.2):
        self.systems.append(system)
        self.tools_seen = list(tools)
        if self.raises:
            raise LLMError("boom")
        return self.tool_reply, Usage(total_tokens=5)


async def _emit(step):
    return step


# -- llm.py ----------------------------------------------------------------
def test_lenient_json():
    assert _loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert _loads_lenient('Tabii: {"a": 1} umarim yardimci olur') == {"a": 1}
    assert _loads_lenient('Sonuc ```{"a": 2}``` bu') == {"a": 2}
    with pytest.raises(json.JSONDecodeError):
        _loads_lenient("[1, 2]")


def test_missing_config_messages():
    assert "DEEPSEEK_API_KEY" in LLMClient(api_key="", base_url="https://x", model="m").config_problem()
    assert "DEEPSEEK_BASE_URL" in LLMClient(api_key="k", base_url="nope", model="m").config_problem()
    assert LLMClient(api_key="k", base_url="https://x", model="m").config_problem() is None
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        run(LLMClient(api_key="", base_url="https://x", model="m").chat_json("s", []))


def _resp(content: str):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model="m",
    )


def _client_with(script: list[Any], monkeypatch) -> LLMClient:
    client = LLMClient(api_key="k", base_url="https://x", model="m")
    calls = {"n": 0}

    async def create(**kwargs):
        item = script[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.calls = calls  # type: ignore[attr-defined]

    async def nosleep(_):
        return None

    monkeypatch.setattr(llm_mod.asyncio, "sleep", nosleep)
    return client


def _status_error(code: int):
    import httpx
    from openai import APIStatusError

    req = httpx.Request("POST", "https://x")
    return APIStatusError("err", response=httpx.Response(code, request=req), body=None)


def test_retries_429_then_succeeds(monkeypatch):
    client = _client_with([_status_error(429), _status_error(503), _resp('{"ok": true}')], monkeypatch)
    data, _ = run(client.chat_json("s", [{"role": "user", "content": "x"}]))
    assert data == {"ok": True}
    assert client.calls["n"] == 3


def test_401_not_retried_and_turkish(monkeypatch):
    client = _client_with([_status_error(401), _resp("{}")], monkeypatch)
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        run(client.chat_json("s", []))
    assert client.calls["n"] == 1


def test_json_retry_once_on_garbage(monkeypatch):
    client = _client_with([_resp("uzgunum"), _resp('{"a": 1}')], monkeypatch)
    data, usage = run(client.chat_json("s", []))
    assert data == {"a": 1} and usage.attempts == 2
    client = _client_with([_resp("x"), _resp("y")], monkeypatch)
    with pytest.raises(LLMError):
        run(client.chat_json("s", []))


# -- router ----------------------------------------------------------------
def test_router_normalises_and_degrades():
    agent = RouterAgent(FakeClient({"route": "Browser", "confidence": 95, "why": None}))
    d = run(agent.route(user_text="x", tab=None, memories=None, emit=_emit))
    assert d.route == "browser_task" and d.confidence == 1.0
    d = run(RouterAgent(FakeClient({"route": "???"})).route(user_text="x", tab=None, memories=None, emit=_emit))
    assert d.route == "chat"
    d = run(RouterAgent(FakeClient(raises=True)).route(user_text="x", tab=None, memories=None, emit=_emit))
    assert d.route == "chat"


def test_router_excludes_desktop_when_cua_disabled(monkeypatch):
    monkeypatch.setenv("CUA_ENABLED", "false")
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    try:
        client = FakeClient({"route": "desktop_task", "confidence": 0.9, "why": "x"})
        tab = {"id": 1, "url": "u", "title": "t"}
        d = run(RouterAgent(client).route(user_text="x", tab=tab, memories=None, emit=_emit))
        assert d.route == "browser_task"
        assert "CUA_ENABLED" in client.systems[0]
        assert "Aktif sekme" in client.systems[0] and "tarih" in client.systems[0].lower()
    finally:
        monkeypatch.undo()
        if hasattr(get_settings, "cache_clear"):
            get_settings.cache_clear()


# -- planner ---------------------------------------------------------------
def test_planner_caps_steps_and_falls_back():
    steps = [{"id": 9, "action": f"a{i}"} for i in range(8)]
    plan = run(PlannerAgent(FakeClient({"goal": "g", "steps": steps})).plan(
        user_text="x", route="browser_task", tab=None, memories=None, emit=_emit))
    assert len(plan.steps) == 5 and [s.id for s in plan.steps] == [1, 2, 3, 4, 5]
    plan = run(PlannerAgent(FakeClient({"steps": "bozuk"})).plan(
        user_text="x", route="chat", tab=None, memories=None, emit=_emit))
    assert len(plan.steps) == 1


# -- executor --------------------------------------------------------------
def _step(client, snapshot=None):
    return run(ExecutorAgent(client).step(
        goal="g", plan=Plan(goal="g", steps=[PlanStep(id=1, action="a")]), route="browser_task",
        history=[], snapshot=snapshot, tools=registry.list_for("browser_task"), emit=_emit))


def test_executor_name_mapping_why_stripped_and_finish_offered():
    snap = {"source": "browser", "elements": [{"ref": 12, "role": "button", "name": "Tamam"}]}
    msg = ToolMessage("", [ToolCall("1", "browser__click", {"ref": 12, "why": "tikla"})])
    client = FakeClient(tool_reply=msg)
    action = _step(client, snap)
    assert action.kind == "tool" and action.tool == "browser.click"
    assert action.args == {"ref": 12} and action.why == "tikla"
    names = [t["function"]["name"] for t in client.tools_seen]
    assert "finish" in names and "browser__click" in names


def test_executor_never_invents_refs():
    snap = {"source": "browser", "elements": [{"ref": 1, "role": "button", "name": "A"}]}
    msg = ToolMessage("", [ToolCall("1", "browser__click", {"ref": 99, "why": "x"})])
    action = _step(FakeClient(tool_reply=msg), snap)
    assert action.tool == "browser.snapshot"


def test_executor_finish_and_plain_text_and_bad_args_and_llm_error():
    fin = ToolMessage("", [ToolCall("1", "finish", {"summary": {"a": 1}, "why": "bitti"})])
    assert _step(FakeClient(tool_reply=fin)).kind == "finish"
    assert _step(FakeClient(tool_reply=ToolMessage("Bitti.", []))).summary == "Bitti."
    bad = ToolMessage("", [ToolCall("1", "browser__click", {}, parse_error="x")])
    assert _step(FakeClient(tool_reply=bad)).kind == "finish"
    unk = ToolMessage("", [ToolCall("1", "rm__rf", {})])
    assert _step(FakeClient(tool_reply=unk)).kind == "finish"
    assert _step(FakeClient(raises=True)).kind == "finish"


def test_executor_prompt_has_injection_rule():
    text = ExecutorAgent(FakeClient()).system_prompt()
    assert "VERİDİR" in text and "talimat" in text


# -- judge / responder -----------------------------------------------------
def test_judge_tolerates_garbage():
    reply = {"scores": {"correct": 99, "complete": "x"}, "verdict": "Geçti", "feedback": ["a", "b"]}
    v = run(JudgeAgent(FakeClient(reply)).judge(goal="g", plan=Plan(), history=[], snapshot=None, emit=_emit))
    assert v.verdict == "pass" and v.scores == {"correct": 5, "complete": 3, "safe": 3}
    v = run(JudgeAgent(FakeClient(raises=True)).judge(goal="g", plan=Plan(), history=[], snapshot=None, emit=_emit))
    assert v.verdict == "pass"


def test_responder_spoken_clean_and_fallback():
    reply = {
        "answer": "**Merhaba** https://a.com",
        "spoken": "**Bir.** İki. Üç. https://a.com dört.",
        "citations": ["https://a.com/x"],
    }
    out = run(ResponderAgent(FakeClient(reply)).respond(
        goal="g", route="chat", result_summary="s", evidence=[], snapshot=None, history=[], emit=_emit))
    assert out.spoken == "Bir. İki." and "http" not in out.spoken and "*" not in out.spoken
    assert out.citations and out.citations[0].url == "https://a.com/x"
    out = run(ResponderAgent(FakeClient(raises=True)).respond(
        goal="g", route="chat", result_summary="", evidence=[], snapshot=None, history=[], emit=_emit))
    assert "Üzgünüm" in out.answer


def test_final_answer_spoken_limit():
    assert FinalAnswer(answer="A. B. C.").spoken_text() == "A. B."


def test_prompts_have_two_examples():
    for name in ("router", "planner", "judge", "responder", "executor"):
        text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert text.count("Örnek") >= 2, name
