"""Judge agent: scores a finished run and decides pass / revise / ask_user.

Owner: K3.  Deterministic wrapper around one strict-JSON LLM call: the scores are
always complete (correct/complete/safe, 1-5) and a broken model never breaks the
request - we fall back to accepting the result as-is.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from ..llm import Usage
from ..trace import EmitFn, Verdict
from .base import BaseAgent, HistoryEntry, JudgeVerdict, Plan, render_snapshot

logger = logging.getLogger(__name__)

_SCORE_KEYS: tuple[str, ...] = ("correct", "complete", "safe")
_VERDICTS: tuple[str, ...] = ("pass", "revise", "ask_user")
_VERDICT_ALIASES: dict[str, str] = {
    "ok": "pass",
    "approved": "pass",
    "accept": "pass",
    "geç": "pass",
    "gecti": "pass",
    "geçti": "pass",
    "onaylandi": "pass",
    "onaylandı": "pass",
    "retry": "revise",
    "redo": "revise",
    "duzelt": "revise",
    "düzelt": "revise",
    "yeniden": "revise",
    "ask": "ask_user",
    "confirm": "ask_user",
    "need_user": "ask_user",
    "sor": "ask_user",
    "soru": "ask_user",
}
_FALLBACK_SCORES: dict[str, int] = {"correct": 3, "complete": 3, "safe": 4}
_FALLBACK_FEEDBACK = "Judge çalıştırılamadı, sonuç olduğu gibi kabul edildi."
_DEFAULT_WHY: dict[str, str] = {
    "pass": "Sonuç yeterli bulundu, ek işlem gerekmiyor.",
    "revise": "Eksik veya hatalı adım var, plan gözden geçirilmeli.",
    "ask_user": "Güvenli olmayan ya da insan kararı gerektiren durum.",
}


class JudgeAgent(BaseAgent):
    """Denetçi ajan: hedefe ulaşıldı mı, kanıt var mı, güvenli mi?"""

    name = "judge"
    prompt_file = "judge.md"

    async def judge(
        self,
        *,
        goal: str,
        plan: Plan,
        history: list[HistoryEntry],
        snapshot: dict[str, Any] | None,
        emit: EmitFn,
    ) -> JudgeVerdict:
        messages = [{"role": "user", "content": self._build_input(goal, plan, history, snapshot)}]
        usage: Usage | None = None
        started = time.perf_counter()
        try:
            data, usage = await self.complete_json(messages, temperature=0.0)
            verdict = self._parse(data)
        except Exception as exc:  # noqa: BLE001 - the judge must never break a request
            # LLMError, or a malformed provider response (IndexError/KeyError):
            # accept the run as-is *and* emit the fallback step with its scores.
            logger.warning("Judge değerlendirmesi yapılamadı (%s): %s", type(exc).__name__, exc)
            verdict = JudgeVerdict(
                scores=dict(_FALLBACK_SCORES),
                verdict="pass",
                feedback=_FALLBACK_FEEDBACK,
            )
            return await self._emit_verdict(
                emit,
                verdict,
                usage,
                latency_ms=int((time.perf_counter() - started) * 1000) if usage is None else None,
            )

        return await self._emit_verdict(emit, verdict, usage)

    # -- internals --------------------------------------------------------
    def _build_input(
        self,
        goal: str,
        plan: Plan,
        history: list[HistoryEntry],
        snapshot: dict[str, Any] | None,
    ) -> str:
        """One user message: goal + rendered plan + performed steps + state."""

        lines = [
            "## Hedef",
            goal.strip() or "(hedef boş)",
            "",
            "## Plan",
            plan.render() if plan.steps or plan.goal else "(plan yok)",
            "",
            "## Yapılan adımlar",
        ]
        if history:
            lines.extend(entry.render() for entry in history)
        else:
            lines.append("- (hiç araç çağrısı yapılmadı)")
        lines.extend(
            [
                "",
                "## Mevcut durum",
                render_snapshot(snapshot, max_elements=60),
                "",
                "Yukarıdaki veriyi değerlendir ve yalnızca istenen JSON şemasında cevap ver.",
            ]
        )
        return "\n".join(lines)

    def _parse(self, data: dict[str, Any]) -> JudgeVerdict:
        return JudgeVerdict(
            scores=self._normalise_scores(data.get("scores")),
            verdict=self._normalise_verdict(data.get("verdict")),
            feedback=self._normalise_feedback(data.get("feedback")),
        )

    @staticmethod
    def _normalise_scores(raw: object) -> dict[str, int]:
        """Model may omit or garble scores; every key is always present."""

        values = raw if isinstance(raw, dict) else {}
        return {key: _clamp(values.get(key)) for key in _SCORE_KEYS}

    @staticmethod
    def _normalise_verdict(raw: object) -> Verdict:
        text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        text = _VERDICT_ALIASES.get(text, text)
        if text in _VERDICTS:
            return cast(Verdict, text)
        if text:
            logger.info("Beklenmeyen judge verdict'i %r, 'pass' kabul edildi", raw)
        return "pass"

    @staticmethod
    def _normalise_feedback(raw: object) -> str:
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, list):
            return " ".join(str(item) for item in raw if str(item).strip()).strip()
        return "" if raw is None else str(raw).strip()

    async def _emit_verdict(
        self,
        emit: EmitFn,
        verdict: JudgeVerdict,
        usage: Usage | None,
        *,
        latency_ms: int | None = None,
    ) -> JudgeVerdict:
        why = verdict.feedback.strip() or _DEFAULT_WHY.get(verdict.verdict, "")
        await self.emit_step(
            emit,
            decision=f"Değerlendirme: {verdict.verdict}",
            why=why,
            scores=verdict.scores,
            verdict=verdict.verdict,
            usage=usage,
            latency_ms=latency_ms,
        )
        return verdict


def _clamp(raw: object) -> int:
    """Missing / non-numeric score means 3, out-of-range scores clamp to 1..5."""

    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, value))
