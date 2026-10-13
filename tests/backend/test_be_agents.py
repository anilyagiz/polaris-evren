"""Agents must degrade gracefully on malformed / failing model output."""
import pytest

from backend.agents.base import (
    FinalAnswer, HistoryEntry, JudgeVerdict, Plan, PlanStep, RouterDecision, BaseAgent,
)
from backend.agents.judge import JudgeAgent
from backend.agents.planner import PlannerAgent
from backend.agents.responder import FALLBACK_ANSWER, ResponderAgent
from backend.agents.router import RouterAgent, _clamp_confidence
from backend.llm import LLMError


@pytest.fixture(autouse=True)
def cua_off(settings_env):
    settings_env()  # defaults: CUA off, no key


async def route(client, tab=None, emit=None):
    return await RouterAgent(client).route(user_text="merhaba", tab=tab, memories=None, emit=emit)


async def test_router_happy(scripted, emitted):
    d = await route(scripted({"route": "web_search", "confidence": 0.8, "why": "araştırma"}), emit=emitted)
    assert d.route == "web_search" and d.confidence == 0.8
    step = emitted.steps[0]
    assert step.agent == "router" and step.decision == "Yönlendirme: web araması" and step.confidence == 0.8


@pytest.mark.parametrize("bad", [
    LLMError("down"),
    IndexError("provider"),
    {"route": "teleport", "confidence": 0.9, "why": "x"},   # unknown route
    {},
    {"route": ["chat"]},
])
async def test_router_falls_back_to_chat(scripted, emitted, bad):
    d = await route(scripted(bad), emit=emitted)
    assert d.route == "chat" and d.confidence <= 0.5
    assert len(emitted.steps) == 1 and emitted.steps[0].agent == "router" and emitted.steps[0].why


async def test_router_cleans_messy_confidence(scripted, emitted):
    d = await route(scripted({"route": "chat", "confidence": 95, "why": None}), emit=emitted)
    assert d.route == "chat" and d.confidence == 1.0
    assert _clamp_confidence("abc") == 0.5 and _clamp_confidence(True) == 0.5
    assert _clamp_confidence(float("nan")) == 0.5 and _clamp_confidence(-3) == 0.0


async def test_router_aliases(scripted, emitted):
    d = await route(scripted({"route": "Web Search", "confidence": 0.5, "why": "w"}), emit=emitted)
    assert d.route == "web_search"


async def test_router_desktop_downgrade_when_cua_off(scripted, emitted):
    d = await route(scripted({"route": "desktop_task", "confidence": 0.9, "why": "w"}), tab={"url": "https://a"}, emit=emitted)
    assert d.route == "browser_task" and d.confidence <= 0.4
    d = await route(scripted({"route": "desktop_task", "confidence": 0.9, "why": "w"}), tab=None, emit=emitted)
    assert d.route == "chat"


async def test_router_desktop_kept_when_cua_on(settings_env, scripted, emitted):
    settings_env(CUA_ENABLED="true")
    d = await route(scripted({"route": "desktop_task", "confidence": 0.9, "why": "w"}), emit=emitted)
    assert d.route == "desktop_task"


async def judge(client, emit):
    return await JudgeAgent(client).judge(goal="g", plan=Plan(goal="g", steps=[PlanStep(id=1, action="a")]),
                                          history=[HistoryEntry(tool="browser.read", args={}, ok=True, data={"x": 1}, error=None, why="w")]
                                          if False else [], snapshot=None, emit=emit)


@pytest.mark.parametrize("bad", [LLMError("x"), KeyError("k"), IndexError("i")])
async def test_judge_fallback_passes(scripted, emitted, bad):
    v = await judge(scripted(bad), emitted)
    assert v.verdict == "pass" and set(v.scores) == {"correct", "complete", "safe"}
    s = emitted.steps[0]
    assert s.agent == "judge" and s.verdict == "pass" and s.scores is not None


async def test_judge_normalises_garbage(scripted, emitted):
    v = await judge(scripted({"scores": {"correct": 99, "complete": "x", "safe": 0}, "verdict": "Düzelt", "feedback": ["a", "b"]}), emitted)
    assert v.verdict == "revise" and v.scores == {"correct": 5, "complete": 3, "safe": 1}
    assert v.feedback == "a b"
    assert emitted.steps[0].verdict == "revise" and emitted.steps[0].scores.correct == 5


async def test_judge_missing_fields_and_unknown_verdict(scripted, emitted):
    v = await judge(scripted({"verdict": "banana"}), emitted)
    assert v.verdict == "pass" and v.scores == {"correct": 3, "complete": 3, "safe": 3}
    v = await judge(scripted({"scores": "bad", "verdict": "ask-user"}), emitted)
    assert v.verdict == "ask_user"


async def test_planner_valid_plan_renumbers_and_caps(scripted, emitted):
    steps = [{"id": 9, "action": f"s{i}", "why": ""} for i in range(8)]
    plan = await PlannerAgent(scripted({"goal": "G", "steps": steps})).plan(
        user_text="x", route="web_search", tab=None, memories=None, emit=emitted)
    assert plan.goal == "G" and len(plan.steps) == 5 and [s.id for s in plan.steps] == [1, 2, 3, 4, 5]
    assert emitted.steps[0].agent == "planner"


@pytest.mark.parametrize("bad", [
    LLMError("x"), {"goal": "", "steps": [{"id": 1, "action": "a"}]}, {"goal": "g", "steps": []},
    {"goal": "g", "steps": [{"nope": 1}]}, {"steps": "x"},
])
async def test_planner_fallback_single_step(scripted, emitted, bad):
    plan = await PlannerAgent(scripted(bad)).plan(user_text="  yap şunu ", route="browser_task", tab=None, memories=None, emit=emitted)
    assert plan.goal == "yap şunu" and len(plan.steps) == 1 and plan.steps[0].id == 1
    assert len(emitted.steps) == 1


async def respond(client, emit, **kw):
    return await ResponderAgent(client).respond(
        goal="g", route="chat", result_summary="s", evidence=kw.get("evidence", []),
        snapshot=None, history=[], emit=emit)


async def test_responder_llm_failure_gives_fallback(scripted, emitted):
    ans = await respond(scripted(LLMError("x")), emitted)
    assert ans.answer == FALLBACK_ANSWER and ans.spoken and ans.citations == []
    assert emitted.steps[0].agent == "responder"


async def test_responder_cleans_spoken_and_citations(scripted, emitted):
    data = {"answer": "**Merhaba** [site](https://a.com)", "spoken": "Bir. İki. Üç. https://x.com",
            "citations": ["sadece-string", {"title": "T", "url": "https://t.com"}, 5]}
    ans = await respond(scripted(data), emitted)
    assert ans.spoken == "Bir. İki."
    assert "https://" not in ans.spoken
    assert any(c.url == "https://t.com" for c in ans.citations)


async def test_responder_empty_object_still_answers(scripted, emitted):
    ans = await respond(scripted({}), emitted)
    assert ans.answer and ans.spoken


def test_model_validators():
    assert RouterDecision(route="Browser", confidence=0.1).route == "browser_task"
    with pytest.raises(Exception):
        RouterDecision(route="x")
    assert JudgeVerdict(scores="junk").scores == {}
    assert JudgeVerdict(scores={"correct": 9, "complete": "z"}).scores == {"correct": 5, "complete": 3, "safe": 3}
    assert FinalAnswer(citations="nope").citations == []
    assert FinalAnswer(spoken="A. B. C.").spoken_text() == "A. B."
    assert FinalAnswer(answer="# Başlık `x`").spoken_text() == "Başlık x"


def test_base_agent_prompt_missing_file_falls_back(tmp_path):
    class A(BaseAgent):
        name = "router"
        prompt_file = "nope.md"

    assert "router" in A(client=None, prompts_dir=tmp_path).system_prompt()
    (tmp_path / "ok.md").write_text("PROMPT", encoding="utf-8")

    class B(BaseAgent):
        prompt_file = "ok.md"

    assert B(client=None, prompts_dir=tmp_path).system_prompt("CTX") == "PROMPT\n\nCTX"
