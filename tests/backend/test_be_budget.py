"""Budget exhaustion finalizes gracefully; repeated judge revisions stop early."""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

from backend import orchestrator as orch
from backend.agents.base import ExecutorAction, FinalAnswer, Plan
from backend.trace import TraceStore


class Router:
    def __init__(self, route="browser_task"):
        self.route_name = route

    async def route(self, **_kw):
        return SimpleNamespace(route=self.route_name, confidence=0.9, why="w")


class Planner:
    async def plan(self, **_kw):
        return Plan(goal="hedef")


class SlowExecutor:
    async def step(self, **_kw):
        await asyncio.sleep(10)


class FinishExecutor:
    def __init__(self):
        self.turns = 0

    async def step(self, **_kw):
        self.turns += 1
        return ExecutorAction(kind="finish", summary=f"özet {self.turns}")


class Judge:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = 0

    async def judge(self, **_kw):
        self.calls += 1
        verdict, feedback = self.verdicts.pop(0) if self.verdicts else ("pass", "")
        return SimpleNamespace(verdict=verdict, feedback=feedback)


class Responder:
    def __init__(self):
        self.summaries = []

    async def respond(self, **kw):
        self.summaries.append(kw["result_summary"])
        return FinalAnswer(answer=f"Cevap: {kw['result_summary']}", spoken="tamam")


def _orch(tmp_path, *, budget=90.0, executor=None, judge=None):
    sent = []

    async def send(frame):
        sent.append(frame)

    o = orch.Orchestrator(send, store=TraceStore(str(tmp_path / "b.db")), client=SimpleNamespace(configured=True))
    o._settings = dataclasses.replace(o._settings, request_budget_s=budget, max_revisions=2)
    o.router, o.planner, o.responder = Router(), Planner(), Responder()
    o.executor = executor or FinishExecutor()
    o.judge = judge or Judge([])
    return o, sent


async def test_budget_exhaustion_sends_partial_final_not_cancel(tmp_path):
    o, sent = _orch(tmp_path, budget=1.2, executor=SlowExecutor())
    await asyncio.wait_for(o._run_request("r_b", "klavye ara", "text", None), timeout=15)
    await o.close()
    types = [f["type"] for f in sent]
    assert "final" in types and "error" not in types
    final = next(f for f in sent if f["type"] == "final")
    assert final["answer"].startswith(orch.PARTIAL_PREFIX)
    decisions = [f["step"]["decision"] for f in sent if f["type"] == "trace"]
    assert not any("iptal" in d.lower() for d in decisions)
    whys = [f["step"]["why"] for f in sent if f["type"] == "trace"]
    assert not any("durdurdu" in w for w in whys)
    assert o.responder.summaries and o.responder.summaries[0].startswith(orch.PARTIAL_PREFIX)


async def test_budget_exhaustion_without_responder_answer_uses_fallback(tmp_path):
    o, sent = _orch(tmp_path, budget=1.2, executor=SlowExecutor())

    class BadResponder:
        async def respond(self, **_kw):
            raise RuntimeError("model düştü")

    o.responder = BadResponder()
    # chat route must still use the normal responder; force tool route
    await asyncio.wait_for(o._run_request("r_c", "x", "text", None), timeout=15)
    await o.close()
    final = next(f for f in sent if f["type"] == "final")
    assert final["answer"].startswith(orch.PARTIAL_PREFIX)


async def test_repeated_revise_feedback_stops_revisions(tmp_path):
    judge = Judge([("revise", "Fiyatları ekle."), ("revise", "fiyatları  ekle"), ("revise", "x")])
    o, sent = _orch(tmp_path, judge=judge)
    await o._run_request("r_r", "klavye", "text", None)
    await o.close()
    assert judge.calls == 2 and o.executor.turns == 2
    assert any(f["type"] == "final" for f in sent)
    assert any(f["type"] == "trace" and f["step"]["decision"] == "Revizyon durduruldu" for f in sent)


async def test_distinct_revise_feedback_still_revises(tmp_path):
    judge = Judge([("revise", "Fiyatları ekle."), ("revise", "Kaynak linklerini de göster."), ("pass", "")])
    o, sent = _orch(tmp_path, judge=judge)
    await o._run_request("r_d", "klavye", "text", None)
    await o.close()
    assert judge.calls == 3 and o.executor.turns == 3


def test_is_repeated_feedback():
    assert orch.is_repeated_feedback("Fiyatları ekle.", "fiyatları ekle")
    assert not orch.is_repeated_feedback(None, "Fiyatları ekle.")
    assert not orch.is_repeated_feedback("Fiyatları ekle.", "Kaynak linklerini göster.")
