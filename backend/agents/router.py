"""RouterAgent: assigns one user message to exactly one route.  Owner: K3.

The orchestrator's very first decision point.  It is deliberately cheap and
deterministic: temperature 0, strict JSON, and a safe `chat` fallback so a
broken model call can never stall a request.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import get_settings
from ..llm import Usage
from ..trace import EmitFn
from .base import BaseAgent, RouterDecision, build_context_block

logger = logging.getLogger(__name__)

#: Appended to the system prompt when CUA is off, so the model never picks a
#: route the backend cannot serve.
_NO_DESKTOP_NOTE = (
    "- ÖNEMLİ: Bu oturumda CUA_ENABLED kapalı. 'desktop_task' seçeneği YOK; "
    "masaüstünde işlem gerekiyorsa 'browser_task' veya 'chat' seç."
)

_FALLBACK_WHY = "Router kararı alınamadı, güvenli varsayılan: chat."


class RouterAgent(BaseAgent):
    """Single-message classifier: `user_text` -> `RouterDecision`."""

    name = "router"
    prompt_file = "router.md"

    async def route(
        self,
        *,
        user_text: str,
        tab: dict[str, Any] | None,
        memories: list[dict[str, Any]] | None,
        emit: EmitFn,
    ) -> RouterDecision:
        settings = get_settings()
        context_block = build_context_block(tab=tab, memories=memories)
        if not settings.cua_enabled:
            context_block = f"{context_block}\n{_NO_DESKTOP_NOTE}"

        messages: list[dict[str, Any]] = [{"role": "user", "content": (user_text or "").strip()}]
        started = time.perf_counter()
        decision: RouterDecision
        usage: Usage | None = None
        degraded = False

        try:
            data, usage = await self.complete_json(
                messages, temperature=0.0, context_block=context_block
            )
            decision = RouterDecision.model_validate(_clean_decision(data))
            decision = self._downgrade_desktop(decision, tab=tab, cua_enabled=settings.cua_enabled)
        except Exception as exc:  # noqa: BLE001 - a broken model call must not stall us
            # LLMError, pydantic ValidationError, or a malformed-but-"valid"
            # provider response (IndexError/KeyError/AttributeError inside llm.py):
            # every one of them degrades to the safe `chat` route *with* a trace.
            logger.warning("Router seçilemedi (%s): %s", type(exc).__name__, exc)
            decision = RouterDecision(route="chat", confidence=0.2, why=_FALLBACK_WHY)
            degraded = True

        await self.emit_step(
            emit,
            decision=f"Route: {decision.route}",
            why=decision.why.strip()
            or (_FALLBACK_WHY if degraded else "Model gerekçe belirtmedi."),
            confidence=decision.confidence,
            usage=usage,
            latency_ms=None
            if usage is not None
            else int((time.perf_counter() - started) * 1000),
        )
        return decision

    @staticmethod
    def _downgrade_desktop(
        decision: RouterDecision, *, tab: dict[str, Any] | None, cua_enabled: bool
    ) -> RouterDecision:
        """CUA kapalıyken `desktop_task` deterministik olarak düşürülür."""

        if cua_enabled or decision.route != "desktop_task":
            return decision
        # Deterministic: an active tab means the work is doable in the browser.
        target = "browser_task" if tab else "chat"
        why = f"{decision.why.strip()} | Masaüstü kapalı olduğu için {target} rotasına düşürüldü."
        logger.info("desktop_task -> %s (CUA kapalı)", target)
        return RouterDecision(
            route=target,
            confidence=min(decision.confidence, 0.4),
            why=why.strip(),
        )


def _clean_decision(data: Any) -> dict[str, Any]:
    """Sanitise a usable-but-messy classifier answer before validating it.

    Without this, `confidence: 95` or a non-string `why` makes pydantic raise
    and throws away an otherwise correct route (the whole decision would drop to
    the `chat` fallback).  An unknown `route` still fails validation on purpose.
    """

    if not isinstance(data, dict):
        return {}
    cleaned = dict(data)
    cleaned["confidence"] = _clamp_confidence(data.get("confidence"))
    why = data.get("why")
    cleaned["why"] = "" if why is None else (why if isinstance(why, str) else str(why))
    route = data.get("route")
    if route is not None and not isinstance(route, str):
        cleaned["route"] = str(route)
    return cleaned


def _clamp_confidence(raw: Any) -> float:
    """`confidence` is a 0.0-1.0 float (router.md); anything else means 0.5."""

    if isinstance(raw, bool) or raw is None:
        return 0.5
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if value != value:  # NaN
        return 0.5
    return max(0.0, min(1.0, value))
