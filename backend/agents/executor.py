"""Executor agent: turns a plan into exactly one tool call per turn.

Owner: K3.  Frozen signature:

    async def step(self, *, goal, plan, route, history, snapshot, tools, emit)
        -> ExecutorAction

The executor is the only place where the model may pick a capability, so it is
also the place where the mandated `why` argument is stripped: `why` explains the
action in the timeline and must never reach a tool handler.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import now_iso
from ..llm import LLMClient, ToolMessage, Usage
from ..tools import registry
from ..tools.registry import Tool
from ..trace import EmitFn
from .base import CONVERSATION_BLOCK, REQUEST_MEMORIES, BaseAgent, ExecutorAction, HistoryEntry, Plan, render_snapshot

logger = logging.getLogger(__name__)

#: Temperature comes from the frozen contract (executor = 0.2).
EXECUTOR_TEMPERATURE = 0.2
MAX_SUMMARY_CHARS = 1000
#: Reserved pseudo-tool: the model may signal completion through the tool list.
FINISH_TOOL = "finish"
FINISH_TOOL_NAMES = {FINISH_TOOL, "done", "__finish", "browser__finish"}
DEFAULT_WHY = "Model gerekçe belirtmedi."

#: Function schema for the `finish` pseudo-tool.  `finish` is not a registered
#: capability (nothing is dispatched through the registry), but executor.md
#: instructs the model to call it, so it must be offered in the function list —
#: otherwise the documented finish path is unreachable through function calling
#: and the model keeps calling tools until the budget runs out.
FINISH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FINISH_TOOL,
        "description": (
            "İş tamamlandığında çağır. Başka araç çağırma. `summary` alanına elde "
            "edilen sonucu (varsa engeli) en fazla 2 cümleyle yaz."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Elde edilen sonuç ve varsa engel; en fazla 2 cümle.",
                },
                "why": {
                    "type": "string",
                    "description": "Bu adımı neden attığını tek cümleyle açıkla.",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sonuca dayanak olan öğeler, örn. [\"ref:21\"].",
                },
            },
            "required": ["summary", "why"],
        },
    },
}


class ExecutorAgent(BaseAgent):
    """Function-calling executor guarded by the tool registry."""

    name = "executor"
    prompt_file = "executor.md"

    # -- public API -------------------------------------------------------
    async def step(
        self,
        *,
        goal: str,
        plan: Plan,
        route: str,
        history: list[HistoryEntry],
        snapshot: dict[str, Any] | None,
        tools: list[Tool],
        emit: EmitFn,
    ) -> ExecutorAction:
        """Run one executor turn: emit a `tool` action or a `finish` action."""

        messages = build_messages(goal=goal, plan=plan, route=route, history=history, snapshot=snapshot)
        schemas = [registry.to_llm_schema_with_why(tool) for tool in tools]
        schemas.append(FINISH_SCHEMA)

        try:
            message, usage = await self.client.chat_tools(
                self.system_prompt(self.context_block(route)),
                messages,
                schemas,
                temperature=EXECUTOR_TEMPERATURE,
            )
        except Exception as exc:  # LLMError and friends: never crash a request
            logger.error("Executor LLM çağrısı başarısız: %s", exc)
            summary = f"Model çağrısı başarısız oldu: {exc}"
            await self._emit(emit, decision="İş bitti: model çağrısı başarısız", why=summary, usage=None)
            return ExecutorAction(kind="finish", summary=summary[:MAX_SUMMARY_CHARS])

        return await self._resolve(message, usage, tools, emit, snapshot)

    def context_block(self, route: str) -> str:
        block = (
            "## Oturum\n"
            f"- Tarih/saat: {now_iso()}\n"
            f"- Yönlendirme (route): {route}\n"
            "- Unutma: her araç çağrısında `why` zorunlu."
        )
        memories = REQUEST_MEMORIES.get()
        if memories:
            facts = "\n".join(f"  - {str(m.get('text', ''))[:200]}" for m in memories[:6])
            block = f"{block}\n- Kullanıcı hakkında hafıza kayıtları:\n{facts}"
        history = CONVERSATION_BLOCK.get()
        return f"{block}\n\n{history}" if history else block

    # -- internals --------------------------------------------------------
    async def _resolve(
        self,
        message: ToolMessage,
        usage: Usage,
        tools: list[Tool],
        emit: EmitFn,
        snapshot: dict[str, Any] | None = None,
    ) -> ExecutorAction:
        call = message.tool_calls[0] if message.tool_calls else None

        # 1) Plain text answer without any tool call -> the work is done.
        if call is None:
            summary = _as_text(message.content) or "İş tamamlandı."
            await self._emit(
                emit,
                decision=f"İş bitti: {_clip(summary, 160)}",
                why=summary or DEFAULT_WHY,
                usage=usage,
            )
            return ExecutorAction(kind="finish", summary=summary)

        # 2) Unparsable arguments -> ask the model to retry through the loop.
        if call.parse_error:
            summary = f"Araç argümanları okunamadı: {call.parse_error}"
            await self._emit(
                emit,
                decision="İş bitti: araç argümanları okunamadı",
                why=summary,
                usage=usage,
            )
            return ExecutorAction(kind="finish", summary=summary[:MAX_SUMMARY_CHARS])

        canonical = registry.from_llm_name(call.name or "")
        args = dict(call.args or {})
        why = str(args.pop("why", "") or "").strip()

        # 3) `finish` signal: accepted independently of the tool list.
        if _is_finish(call.name) or canonical == FINISH_TOOL:
            # `summary` comes from the model: it may be a string, a number or an
            # object, so it is coerced instead of trusting `str.strip()`.
            summary = _as_text(message.content) or _as_text(args.get("summary")) or "İş tamamlandı."
            evidence = _clean_evidence(args.get("evidence"))
            await self._emit(
                emit,
                decision=f"İş bitti: {_clip(summary, 160)}",
                why=why or summary or DEFAULT_WHY,
                usage=usage,
                evidence=evidence,
            )
            return ExecutorAction(kind="finish", summary=summary, why=why, evidence=evidence)

        # 4) Unknown tool for this route/registry -> safe stop with a hint.
        tool = registry.get(canonical)
        if tool is None or not _offered(tool, tools):
            available = ", ".join(t.name for t in tools) or "(bu yönlendirmede araç yok)"
            summary = f"Bilinmeyen araç: {call.name}. Mevcut araçlar: {available}"
            await self._emit(
                emit,
                decision=f"İş bitti: bilinmeyen araç {call.name}",
                why=why or summary,
                usage=usage,
                tool={"name": canonical or call.name or "unknown", "args": args},
            )
            return ExecutorAction(kind="finish", summary=summary[:MAX_SUMMARY_CHARS])

        # 4b) Never invent refs: a ref absent from the latest snapshot is replaced
        # by a snapshot refresh (the next turn then sees real refs).
        stale = _stale_ref(tool, args, snapshot)
        if stale is not None:
            refresh = _snapshot_tool(tools)
            if refresh is not None:
                await self._emit(
                    emit,
                    decision=f"Araç çağrısı: {refresh.name}",
                    why=f"ref {stale} son snapshot'ta yok; sayfayı yeniliyorum.",
                    usage=usage,
                    tool={"name": refresh.name, "args": {}},
                )
                return ExecutorAction(
                    kind="tool", tool=refresh.name, args={},
                    why=f"ref {stale} son snapshot'ta yok; sayfayı yeniliyorum.",
                )

        # 5) Real tool call: `why` already removed, handler args stay clean.
        decision = f"Araç çağrısı: {tool.name}"
        evidence = _ref_evidence(tool, args)
        await self._emit(
            emit,
            decision=decision,
            why=why or DEFAULT_WHY,
            usage=usage,
            tool={"name": tool.name, "args": args},
            evidence=evidence,
        )
        return ExecutorAction(
            kind="tool", tool=tool.name, args=args, why=why, summary="", evidence=evidence
        )

    async def _emit(
        self,
        emit: EmitFn,
        *,
        decision: str,
        why: str,
        usage: Usage | None,
        tool: dict[str, Any] | None = None,
        evidence: list[str] | None = None,
    ) -> None:
        try:
            await self.emit_step(
                emit,
                decision=decision,
                why=why or DEFAULT_WHY,
                tool=tool,
                evidence=evidence or None,
                usage=usage,
            )
        except Exception as exc:  # tracing must never break a turn
            logger.warning("Executor trace yayılamadı: %s", exc)


def build_messages(
    *,
    goal: str,
    plan: Plan,
    route: str,
    history: list[HistoryEntry],
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Assemble the single user message the executor sees."""

    history_lines = [entry.render() for entry in history]
    history_block = "\n".join(history_lines) if history_lines else "(henüz araç çağrısı yok)"
    content = "\n\n".join(
        [
            f"## Kullanıcı hedefi\n{goal}",
            f"## Plan\n{plan.render() if plan else '(plan yok)'}",
            f"## Yönlendirme\n{route}",
            f"## HISTORY\n{history_block}",
            f"## SNAPSHOT\n{render_snapshot(snapshot)}",
            (
                "## Görev\n"
                "Yukarıdaki plana göre SIRADAKİ tek adımı seç. Bir araç çağıracaksan `why` "
                "alanını doldur. İş bittiyse `finish` çağır. `ref` son snapshot'ta yoksa "
                "önce `browser.snapshot` çağır."
            ),
        ]
    )
    return [{"role": "user", "content": content}]


_REF_TOOLS = {"browser.click", "browser.type", "browser.highlight", "browser.scroll",
              "desktop.click", "desktop.type"}


def _stale_ref(tool: Tool, args: dict[str, Any], snapshot: dict[str, Any] | None) -> Any:
    """Return the offending ref when it is not in the snapshot, else None."""

    if tool.name not in _REF_TOOLS or "ref" not in args or not snapshot:
        return None
    elements = snapshot.get("elements") or []
    if not elements:
        return None
    known = {str(el.get("ref")) for el in elements if isinstance(el, dict)}
    ref = args.get("ref")
    return None if str(ref) in known else ref


_EVIDENCE_REF_TOOLS = {"browser.click", "browser.type", "browser.read", "browser.highlight"}


def _ref_evidence(tool: Tool, args: dict[str, Any]) -> list[str]:
    """`ref:N` evidence for tool calls that act on a snapshot ref."""

    if tool.name not in _EVIDENCE_REF_TOOLS:
        return []
    try:
        return [f"ref:{int(args.get('ref'))}"]
    except (TypeError, ValueError):
        return []


def _clean_evidence(value: Any) -> list[str]:
    """Model-provided evidence: keep unique non-empty strings, ints become `ref:N`."""

    if isinstance(value, (str, int)):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, bool):
            continue
        text = f"ref:{item}" if isinstance(item, int) else str(item).strip()
        if text.isdigit():
            text = f"ref:{text}"
        if text and text not in out:
            out.append(text[:200])
    return out[:10]


def _snapshot_tool(tools: list[Tool]) -> Tool | None:
    for candidate in tools:
        if candidate.name in ("browser.snapshot", "desktop.snapshot"):
            return candidate
    return None


def _is_finish(name: str | None) -> bool:
    raw = (name or "").strip().lower()
    return raw in FINISH_TOOL_NAMES or raw.replace("__", ".") == FINISH_TOOL


def _as_text(value: Any, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Coerce a model-provided summary (str, dict, list, number) into plain text.

    A dict `summary` used to raise `AttributeError` straight out of the agent,
    which reached the user as an `error` frame instead of a fallback.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value).strip()
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _offered(tool: Tool, tools: list[Tool]) -> bool:
    """The registry may know a tool the current route does not expose."""

    if not tools:
        return False
    return any(candidate.name == tool.name for candidate in tools)


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


__all__ = ["ExecutorAgent", "build_messages"]
