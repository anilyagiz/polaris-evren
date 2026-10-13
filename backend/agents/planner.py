"""PlannerAgent: turns one routed request into a short, tool-shaped plan.

Owner: K3.  Frozen contract: INTERFACES.md section 8 (temperature 0.2, at most
five steps, one concrete tool call per step).  A planner failure must never
break the request, so it degrades to a single safe step.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..llm import Usage
from ..trace import EmitFn
from ..tools import registry
from .base import BaseAgent, Plan, PlanStep, build_context_block

logger = logging.getLogger(__name__)

#: Degraded single-step plan, used whenever the model cannot be trusted.
FALLBACK_ACTION = "Kullanıcı isteğini karşılamak için uygun araçları kullan"
FALLBACK_WHY = "Plan oluşturulamadı, tek adımlı güvenli plan."


class PlannerAgent(BaseAgent):
    """Produces a <=5 step plan whose steps map to single tool calls."""

    name = "planner"
    prompt_file = "planner.md"
    temperature = 0.2

    async def plan(
        self,
        *,
        user_text: str,
        route: str,
        tab: dict[str, Any] | None,
        memories: list[dict[str, Any]] | None,
        emit: EmitFn,
    ) -> Plan:
        context_block = build_context_block(tab=tab, memories=memories)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._user_prompt(user_text=user_text, route=route)}
        ]

        usage: Usage | None = None
        degraded = False
        started = time.perf_counter()
        try:
            data, usage = await self.complete_json(
                messages,
                temperature=self.temperature,
                context_block=context_block,
            )
            plan = self._parse(data)
        except Exception as exc:  # noqa: BLE001 - a planner failure must not break the request
            # LLMError, a malformed provider response (IndexError/KeyError) or an
            # unusable plan: degrade to one safe step, but keep the tokens/latency
            # of the answer that did arrive so the timeline stays complete.
            logger.warning(
                "Planner plan üretemedi (%s), tek adımlı plana düşülüyor: %s",
                type(exc).__name__,
                exc,
            )
            plan = self._fallback(user_text)
            degraded = True

        await self.emit_step(
            emit,
            decision=self._decision(plan),
            why=self._why(plan, degraded=degraded),
            evidence=[step.action[:80] for step in plan.steps[:3]],
            usage=usage,
            latency_ms=None
            if usage is not None
            else int((time.perf_counter() - started) * 1000),
        )
        return plan

    # -- helpers ----------------------------------------------------------
    def _user_prompt(self, *, user_text: str, route: str) -> str:
        tools = registry.list_for(route)
        names = ", ".join(tool.name for tool in tools) or "(araç yok)"
        return (
            f"Kullanıcı isteği: {user_text}\n"
            f"Router kararı: {route}\n"
            f"Bu rota için kullanılabilir araçlar: {names}\n"
            "İstek için en fazla 5 adımlı planı JSON olarak üret."
        )

    def _parse(self, data: dict[str, Any]) -> Plan:
        """Validate the model output and normalise step ids to 1..N."""

        plan = Plan.model_validate(data)
        if not plan.goal.strip():
            raise ValueError("plan.goal boş")
        if not plan.steps:
            raise ValueError("plan.steps boş")
        for index, step in enumerate(plan.steps, start=1):
            step.id = index
        return plan

    def _fallback(self, user_text: str) -> Plan:
        return Plan(
            goal=(user_text or "").strip()[:200],
            steps=[PlanStep(id=1, action=FALLBACK_ACTION, why=FALLBACK_WHY)],
        )

    @staticmethod
    def _decision(plan: Plan) -> str:
        goal = plan.goal.strip() or "plan"
        return goal[:120]

    @staticmethod
    def _why(plan: Plan, *, degraded: bool) -> str:
        if degraded:
            return "Plan oluşturulamadı; tek adımlı güvenli plan kullanıldı."
        # ASCII-safe separator: the trace text is also logged to Windows consoles
        # whose code page (cp1254) cannot encode arrows such as U+2192.
        summary = " | ".join(step.action[:60] for step in plan.steps[:3])
        return f"{len(plan.steps)} adımlı plan oluşturuldu: {summary}"
