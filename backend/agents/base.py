"""Frozen agent contract: shared pydantic models, prompt loading, tracing.

Owner: K3.  Every agent module (router/planner/executor/judge/responder)
subclasses BaseAgent and implements exactly one `async def` whose signature is
declared in its own module.  The orchestrator only sees these models.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..config import PROMPTS_DIR, get_settings, now_iso
from ..llm import LLMClient, Usage
from ..trace import EmitFn, TraceStep, make_step

logger = logging.getLogger(__name__)

#: False while serving the Polaris desktop window (no browser extension on the
#: other end of the socket).  Set per request task by the orchestrator.
BROWSER_AVAILABLE: ContextVar[bool] = ContextVar("polaris_browser_available", default=True)
NO_BROWSER_NOTE = (
    "- Tarayıcı erişimi yok (masaüstü penceresi): browser.* araçları ve sekme/sayfa işlemleri "
    "kullanılamaz. Tarayıcıda yapılması gereken bir iş istenirse kullanıcıya bunun için "
    "Brave'de Polaris panelini açmasını söyle; sohbet, web araması, hafıza ve hatırlatmalar çalışır."
)

#: Rendered "## Önceki konuşma" block of the current session (set per request
#: task by the orchestrator; empty for a fresh session or outside a request).
CONVERSATION_BLOCK: ContextVar[str] = ContextVar("polaris_conversation_block", default="")
#: Memory records relevant to the current request (search hits + profile facts).
#: Agents that do not receive `memories` explicitly (responder, judge) use these.
REQUEST_MEMORIES: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
    "polaris_request_memories", default=()
)
MAX_CONTEXT_MEMORIES = 6

RouteName = Literal["chat", "web_search", "browser_task", "desktop_task", "personal"]
MAX_PLAN_STEPS = 5


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------
class RouterDecision(BaseModel):
    route: RouteName = "chat"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    why: str = ""

    @field_validator("route", mode="before")
    @classmethod
    def _normalise_route(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
            aliases = {"search": "web_search", "web": "web_search", "browser": "browser_task",
                       "desktop": "desktop_task", "chat_answer": "chat"}
            cleaned = aliases.get(cleaned, cleaned)
            return cleaned
        return value


class PlanStep(BaseModel):
    id: int
    action: str
    why: str = ""


class Plan(BaseModel):
    goal: str = ""
    steps: list[PlanStep] = Field(default_factory=list)

    @field_validator("steps", mode="before")
    @classmethod
    def _cap_steps(cls, value: Any) -> Any:
        if isinstance(value, list):
            return value[:MAX_PLAN_STEPS]
        return value

    def render(self) -> str:
        lines = [f"Hedef: {self.goal}"]
        for step in self.steps:
            lines.append(f"{step.id}. {step.action}" + (f"  (neden: {step.why})" if step.why else ""))
        return "\n".join(lines)


class ExecutorAction(BaseModel):
    """One executor turn: either a tool call or `finish`."""

    kind: Literal["tool", "finish"] = "tool"
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    scores: dict[str, int] = Field(default_factory=dict)
    verdict: Literal["pass", "revise", "ask_user"] = "pass"
    feedback: str = ""

    @field_validator("scores", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        out: dict[str, int] = {}
        for key in ("correct", "complete", "safe"):
            try:
                out[key] = max(1, min(5, int(value.get(key, 3))))
            except (TypeError, ValueError):
                out[key] = 3
        return out


class Citation(BaseModel):
    title: str
    url: str = ""
    ref: int | None = None


class FinalAnswer(BaseModel):
    answer: str = ""
    spoken: str = ""
    citations: list[Citation] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                cleaned.append({"title": item, "url": ""})
            elif isinstance(item, dict):
                cleaned.append(item)
        return cleaned

    def spoken_text(self, limit_sentences: int = 2) -> str:
        """Enforce the '<=2 sentences, no markdown/URL' rule for TTS."""

        import re

        text = (self.spoken or self.answer or "").strip()
        text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"[*_#>`~]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        sentences = re.split(r"(?<=[.!?…])\s+", text)
        return " ".join(sentences[:limit_sentences]).strip()


# ---------------------------------------------------------------------------
# History entry passed to executor / judge
# ---------------------------------------------------------------------------
_HISTORY_LIMITS = {"browser.read": 4000, "web.search": 2500, "memory.search": 1500, "reminders.list": 1500}


@dataclass
class HistoryEntry:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    data: Any = None
    error: str | None = None
    why: str = ""
    rejected: bool = False

    def render(self) -> str:
        status = "ok" if self.ok else f"HATA: {self.error or 'bilinmiyor'}"
        if self.rejected:
            status = "kullanıcı reddetti"
        # Reading tools exist to bring page text to the model: don't starve them.
        limit = _HISTORY_LIMITS.get(self.tool, 600) if self.ok else 600
        body = _short(self.data if self.ok else self.error, limit)
        return f"- {self.tool}({_short(self.args, 160)}) -> {status}: {body}"

    def to_message(self) -> str:
        return self.render()


def _short(value: Any, limit: int = 600) -> str:
    import json

    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# System prompt context (date/time, active tab, memories)
# ---------------------------------------------------------------------------
def build_context_block(
    *,
    tab: dict[str, Any] | None = None,
    memories: list[dict[str, Any]] | None = None,
    include_history: bool = True,
) -> str:
    """Context appended to every agent's system prompt, every request.

    ``memories=None`` falls back to the request's REQUEST_MEMORIES (so the
    responder/judge see them too); the session's recent conversation
    (CONVERSATION_BLOCK) is appended last.
    """

    settings = get_settings()
    if memories is None:
        memories = list(REQUEST_MEMORIES.get())
    lines = [
        "## Bağlam",
        f"- Şu anki tarih/saat: {now_iso()} ({settings.timezone})",
    ]
    if tab:
        title = tab.get("title") or "(başlıksız)"
        url = tab.get("url") or "(bilinmiyor)"
        lines.append(f"- Aktif sekme: #{tab.get('id', '?')} “{title}” — {url}")
    else:
        lines.append("- Aktif sekme bilgisi yok.")
    if memories:
        lines.append(
            "- Kullanıcı hakkında hafıza kayıtları (kullanıcının daha önce söyledikleri; "
            "soruyla ilgiliyse kullan):"
        )
        for item in memories[:MAX_CONTEXT_MEMORIES]:
            text = str(item.get("text", "")).strip()
            lines.append(f"  - ({item.get('id', '?')}) {text[:200]}")
    else:
        lines.append("- İlgili hafıza kaydı yok.")
    if not settings.cua_enabled:
        lines.append("- Masaüstü (desktop_*) yetenekleri bu oturumda KAPALI.")
    if not BROWSER_AVAILABLE.get():
        lines.append(NO_BROWSER_NOTE)
    block = "\n".join(lines)
    if include_history:
        history = CONVERSATION_BLOCK.get()
        if history:
            block = f"{block}\n\n{history}"
    return block


def render_snapshot(snapshot: dict[str, Any] | None, *, max_elements: int = 150) -> str:
    """Same textual form the LLM sees for browser and desktop snapshots."""

    if not snapshot:
        return "(snapshot yok — önce snapshot aracını çağır)"
    head = [f"kaynak: {snapshot.get('source', '?')}"]
    if snapshot.get("url"):
        head.append(f"url: {snapshot['url']}")
    if snapshot.get("title"):
        head.append(f"başlık: {snapshot['title']}")
    if snapshot.get("shot"):
        head.append(f"ekran görüntüsü: {snapshot['shot']}")
    elements = (snapshot.get("elements") or [])[:max_elements]
    lines = [" | ".join(head), "öğeler:"]
    for element in elements:
        role = element.get("role") or "element"
        name = str(element.get("name") or "")[:80]
        value = element.get("value")
        extra = f" = {str(value)[:60]}" if value else ""
        lines.append(f'[{element.get("ref")}] {role} "{name}"{extra}')
    excerpt = (snapshot.get("text_excerpt") or "").strip()
    if excerpt:
        lines.append("metin:")
        lines.append(excerpt[:1500])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------
class AgentError(RuntimeError):
    """Raised when an agent cannot produce a usable answer."""


class BaseAgent:
    """Prompt loading + tracing + JSON completion, shared by all agents."""

    name: str = "system"
    prompt_file: str = ""

    def __init__(self, client: LLMClient, prompts_dir: Path | None = None) -> None:
        self.client = client
        self.prompts_dir = prompts_dir or PROMPTS_DIR
        self._prompt_cache: str | None = None

    # -- prompts ----------------------------------------------------------
    @property
    def prompt_path(self) -> Path:
        return self.prompts_dir / self.prompt_file

    def system_prompt(self, context_block: str = "") -> str:
        if self._prompt_cache is None:
            try:
                self._prompt_cache = self.prompt_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                logger.error("Prompt dosyası yok: %s", self.prompt_path)
                self._prompt_cache = f"Sen {self.name} ajanısın. Kısa ve JSON uyumlu cevap ver."
        if context_block:
            return f"{self._prompt_cache}\n\n{context_block}"
        return self._prompt_cache

    # -- tracing ----------------------------------------------------------
    async def emit_step(
        self,
        emit: EmitFn,
        *,
        decision: str,
        why: str,
        evidence: list[str] | None = None,
        confidence: float | None = None,
        tool: dict[str, Any] | None = None,
        scores: dict[str, Any] | None = None,
        verdict: str | None = None,
        usage: Usage | None = None,
        latency_ms: int | None = None,
    ) -> TraceStep:
        step = make_step(
            self.name,  # type: ignore[arg-type]
            decision,
            why,
            evidence=evidence,
            confidence=confidence,
            tool=tool,
            scores=scores,
            verdict=verdict,  # type: ignore[arg-type]
            latency_ms=latency_ms if latency_ms is not None else (usage.latency_ms if usage else 0),
            tokens=usage.tokens if usage else None,
        )
        return await emit(step)

    # -- LLM --------------------------------------------------------------
    async def complete_json(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        context_block: str = "",
    ) -> tuple[dict[str, Any], Usage]:
        return await self.client.chat_json(
            self.system_prompt(context_block), messages, temperature=temperature
        )
