"""Orchestrator: the request brain of the assistant backend.

Owner: K3.  One instance per WebSocket connection.  It owns the single-request
rule, the deterministic confirmation gate, the tool/budget limits, cancellation
and the explainability trace.  Frozen API: INTERFACES.md section 10.

Agent modules are imported defensively: while `agents/*.py` is still missing the
connection stays alive and every request is answered with an `error` frame.
"""

from __future__ import annotations

import asyncio
import difflib
import importlib
import inspect
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from .agents.base import BROWSER_AVAILABLE, FinalAnswer, HistoryEntry, Plan
from .bridge import BrowserBridge, SendFn, new_call_id
from .config import get_settings
from .llm import LLMClient, get_llm
from .tools import registry
from .tools.registry import Tool, ToolContext
from .trace import EmitFn, TraceStore, make_emitter, make_step

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .agents.executor import ExecutorAgent
    from .agents.judge import JudgeAgent
    from .agents.planner import PlannerAgent
    from .agents.responder import ResponderAgent
    from .agents.router import RouterAgent

logger = logging.getLogger(__name__)

#: Budget for the one-off "judge needs more information" question.
JUDGE_QUESTION_TIMEOUT_S = 60.0
#: Extra slack on top of `settings.tool_timeout_s` for handler-level protection.
TOOL_TIMEOUT_MARGIN_S = 5.0
#: Part of the request budget kept for the Responder so a request that runs out
#: of time still ends with a normal `final` inside the budget.
RESPONDER_RESERVE_S = 30.0
#: Share of the budget the reserve may take at most (small test budgets).
RESPONDER_RESERVE_RATIO = 0.25
#: Judge `revise` feedback this similar to the previous one ends the revisions.
REVISE_SIMILARITY = 0.85
#: Prefix of an answer produced after the budget ran out.
PARTIAL_PREFIX = "Süre doldu, eldeki sonuç:"
#: `hello.client` of the Polaris desktop window: no browser extension behind it.
DESKTOP_CLIENT = "desktop"
#: Tool error for browser.* calls on a desktop-window connection.
NO_BROWSER_ERROR = "Bu işlem tarayıcıda yapılır: Brave'de Polaris panelini aç."


def without_browser_tools(tools: list[Tool]) -> list[Tool]:
    """Desktop window: hide every browser.* tool from the executor."""

    return [tool for tool in tools if not tool.name.startswith("browser.")]


class BudgetExceeded(Exception):
    """The request budget ran out (never a user cancel)."""


def _norm_feedback(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def is_repeated_feedback(previous: str | None, current: str | None) -> bool:
    """Is the judge asking for the same revision again (identical / near-identical)?"""

    a, b = _norm_feedback(previous), _norm_feedback(current)
    if not a or not b:
        return a == b and previous is not None
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= REVISE_SIMILARITY

AGENT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("router", ".agents.router", "RouterAgent"),
    ("planner", ".agents.planner", "PlannerAgent"),
    ("executor", ".agents.executor", "ExecutorAgent"),
    ("judge", ".agents.judge", "JudgeAgent"),
    ("responder", ".agents.responder", "ResponderAgent"),
)

#: Importing these modules is what registers their tools.  Missing files are fine.
TOOL_MODULES: tuple[str, ...] = (
    ".tools.browser",
    ".tools.desktop",
    ".tools.search",
    ".tools.memory",
    ".tools.reminders",
)


class AgentUnavailable(RuntimeError):
    """An agent module is missing, so the request cannot be served."""


class _RequestLogger(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Prefixes every log line with the request id."""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        return f"[{self.extra.get('request_id') if self.extra else '-'}] {msg}", kwargs


@dataclass
class _ToolRun:
    """Outcome of one executor loop (may run several times per request)."""

    calls: int
    summary: str
    evidence: list[str]
    snapshot: dict[str, Any] | None


async def _within(coro: Any, deadline: float) -> Any:
    """Await `coro` but never past `deadline`; raises BudgetExceeded when it would."""

    left = deadline - time.monotonic()
    if left <= 0:
        coro.close()
        raise BudgetExceeded()
    try:
        return await asyncio.wait_for(coro, timeout=left)
    except asyncio.TimeoutError:
        if time.monotonic() >= deadline - 0.05:
            raise BudgetExceeded() from None
        raise  # a timeout raised inside the agent itself


def _load_agent(module_path: str, class_name: str, client: LLMClient) -> Any:
    """Import one agent class and build it; `None` when the module is absent."""

    try:
        module = importlib.import_module(module_path, package=__package__)
        factory = getattr(module, class_name)
    except Exception as exc:  # ModuleNotFoundError, SyntaxError, ...
        logger.info("Ajan modülü yüklenemedi %s.%s: %s", module_path, class_name, exc)
        return None
    try:
        return factory(client)
    except Exception as exc:
        logger.error("Ajan kurulamadı %s: %s", class_name, exc)
        return None


def _new_request_id() -> str:
    return f"r_{secrets.token_hex(3)}"


#: Values accepted as "true" when a protocol frame carries a JSON string instead
#: of a real boolean.  Anything unrecognised counts as false: the confirmation
#: gate and the tool-result path both fail *closed*.
_TRUTHY_TEXT = frozenset({"1", "true", "yes", "on", "evet", "tamam", "approved", "ok"})


def _as_bool(value: Any) -> bool:
    """Strict boolean parsing for `confirm.approved` / `tool_result.ok`.

    `bool("false")` is `True`, so a stringified flag used to be able to approve a
    risky click by accident.  Unknown types are treated as false.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_TEXT
    return False


def _reminder_text(record: Any) -> tuple[str, str]:
    """Turkish notify text for one due reminder."""

    if isinstance(record, dict):
        body = str(record.get("text") or record.get("title") or record.get("message") or "").strip()
        spoken = str(record.get("spoken") or "").strip()
    else:
        body = str(record or "").strip()
        spoken = ""
    if not body:
        return "", ""
    text = f"Hatırlatma: {body}"
    return text, spoken or text


def _as_memories(data: Any, limit: int = 3) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        items = data.get("results") or data.get("items") or data.get("memories") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [item for item in items if isinstance(item, dict)][:limit]


def _normalise_result(tool: Tool, result: dict[str, Any]) -> dict[str, Any]:
    """Enforce the `{"ok", "data", "error"}` handler contract.

    A few handlers hand back a bare payload (e.g. a snapshot dict) instead of
    the wrapper.  Snapshot-shaped payloads are accepted — loudly — so real
    browser data is never thrown away; anything else stays a failure.
    """

    if "ok" in result:
        if result.get("ok") and "data" not in result:
            # PROTOCOL section 7 tools (web.search, memory.*, reminders.*, desktop.*)
            # return flat payloads such as {"ok": True, "results": [...]}.  Move the
            # payload under "data" so the executor/judge/responder see it.
            payload = {k: v for k, v in result.items() if k not in ("ok", "error")}
            if payload:
                return {"ok": True, "data": payload, "error": None}
        return result
    if "elements" in result or "source" in result:
        logger.warning(
            "Araç %s sözleşme dışı sonuç döndürdü ('ok' alanı yok); snapshot olarak kabul edildi",
            tool.name,
        )
        return {"ok": True, "data": result}
    logger.warning("Araç %s sonucu 'ok' alanı içermiyor: %s", tool.name, sorted(result)[:6])
    return {"ok": False, "error": f"araç sonucu geçersiz (ok/data/error bekleniyor): {tool.name}"}


class Orchestrator:
    """Owns one WebSocket conversation: one request at a time, fully traced."""

    def __init__(
        self,
        send: SendFn,
        *,
        store: TraceStore | None = None,
        bridge: BrowserBridge | None = None,
        client: LLMClient | None = None,
    ) -> None:
        self._settings = get_settings()
        self._send = send
        self.store = store or TraceStore()
        self.bridge = bridge or BrowserBridge(send)
        self.client = client or get_llm()

        self.router: RouterAgent | None = _load_agent(".agents.router", "RouterAgent", self.client)
        self.planner: PlannerAgent | None = _load_agent(".agents.planner", "PlannerAgent", self.client)
        self.executor: ExecutorAgent | None = _load_agent(".agents.executor", "ExecutorAgent", self.client)
        self.judge: JudgeAgent | None = _load_agent(".agents.judge", "JudgeAgent", self.client)
        self.responder: ResponderAgent | None = _load_agent(".agents.responder", "ResponderAgent", self.client)

        missing = self.missing_agents()
        if missing:
            logger.warning(
                "Eksik ajan modülleri (%s): bu istekler 'error' frame'i ile yanıtlanacak.",
                ", ".join(missing),
            )

        self._task: asyncio.Task[None] | None = None
        self._task_request_id: str | None = None
        self._reminder_task: asyncio.Task[None] | None = None
        self._announced_cancels: set[str] = set()
        #: Latest page snapshot of the running request; the trace emitter uses it
        #: to mask text typed into password/card fields (one request at a time).
        self._latest_snapshot: dict[str, Any] | None = None
        #: `hello.client` of this connection ("extension", "desktop", "cli"...).
        self.client_kind = ""

    @property
    def browser_available(self) -> bool:
        """False for the desktop window: there is no extension to run browser.* tools."""

        return self.client_kind != DESKTOP_CLIENT

    # -- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        """Prepare storage, register tools and start the reminder loop."""

        await self.store.init()
        self._register_tools()
        if self._reminder_task is None or self._reminder_task.done():
            self._reminder_task = asyncio.create_task(self._reminder_loop(), name="reminder-loop")
        logger.info(
            "Orchestrator hazır — kayıtlı araç: %d, eksik ajan: %s",
            len(registry.all_tools()),
            ", ".join(self.missing_agents()) or "yok",
        )

    async def close(self) -> None:
        """Stop background work; never raises."""

        for task in (self._reminder_task, self._task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.info("Görev kapanışta hata verdi: %s", exc)
        self._reminder_task = None
        self._task = None
        self._task_request_id = None

    def missing_agents(self) -> list[str]:
        return [name for name, _, _ in AGENT_SPECS if getattr(self, name, None) is None]

    @staticmethod
    def _require(agent: Any, name: str) -> Any:
        if agent is None:
            raise AgentUnavailable(f"Gerekli ajan modülü yüklenemedi: {name}")
        return agent

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Wait for the in-flight request (CLI/tests).  Timeout returns quietly."""

        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info("wait_idle: süre doldu, istek hâlâ sürüyor")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info("wait_idle: istek hata ile bitti: %s", exc)

    # -- tool modules -----------------------------------------------------
    def _register_tools(self) -> None:
        for module_path in TOOL_MODULES:
            try:
                importlib.import_module(module_path, package=__package__)
                logger.info("Araç modülü yüklendi: %s", module_path)
            except ImportError as exc:
                logger.info("Araç modülü yok %s: %s", module_path, exc)
            except Exception as exc:  # a broken tool module must not kill the app
                logger.warning("Araç modülü yüklenemedi %s: %s", module_path, exc)

    # -- reminders --------------------------------------------------------
    async def _reminder_loop(self) -> None:
        poll = max(5.0, float(self._settings.reminder_poll_s))
        while True:
            await asyncio.sleep(poll)
            try:
                await self._send_due_reminders()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Hatırlatma döngüsü hatası: %s", exc)

    async def _send_due_reminders(self) -> None:
        try:
            from .tools.reminders import due  # type: ignore[attr-defined]
        except (ImportError, AttributeError) as exc:
            logger.info("Hatırlatma modülü hazır değil: %s", exc)
            return
        records = due()
        if inspect.isawaitable(records):
            records = await records
        for record in records or []:
            text, spoken = _reminder_text(record)
            if not text:
                continue
            logger.info("Hatırlatma gönderiliyor: %s", text)
            try:
                await self._send({"type": "notify", "text": text, "spoken": spoken})
            except Exception as exc:
                logger.info("notify gönderilemedi: %s", exc)
                # Put the reminder back so a live connection can deliver it later.
                rid = record.get("id") if isinstance(record, dict) else None
                if rid is not None:
                    try:
                        from .tools.reminders import unmark  # type: ignore[attr-defined]

                        await unmark([rid])
                    except Exception as exc2:
                        logger.info("Hatırlatma geri alınamadı: %s", exc2)
                return

    # -- frames -----------------------------------------------------------
    async def handle_frame(self, frame: dict[str, Any]) -> None:
        frame_type = str(frame.get("type") or "").strip()

        if frame_type == "hello":
            logger.info("hello alındı: %s", {k: v for k, v in frame.items() if k != "type"})
            self.client_kind = str(frame.get("client") or "").strip().lower()
            if not self.browser_available:
                logger.info("Masaüstü penceresi bağlandı: browser.* araçları kapalı")
            return

        if frame_type == "user_msg":
            await self._on_user_msg(frame)
            return

        if frame_type == "tool_result":
            call_id = str(frame.get("call_id") or "")
            data = frame.get("data") if isinstance(frame.get("data"), dict) else None
            error = frame.get("error")
            if not self.bridge.resolve_tool_result(call_id, _as_bool(frame.get("ok")), data, error):
                logger.info("Eşleşmeyen tool_result yok sayıldı: %s", call_id)
            return

        if frame_type == "confirm":
            call_id = str(frame.get("call_id") or "")
            if not self.bridge.resolve_confirm(call_id, _as_bool(frame.get("approved"))):
                logger.info("Eşleşmeyen confirm yok sayıldı: %s", call_id)
            return

        if frame_type == "cancel":
            await self._on_cancel(frame)
            return

        logger.info("Bilinmeyen frame tipi: %r", frame_type)
        await self._send_error(_frame_id(frame), f"Bilinmeyen frame tipi: {frame_type or '?'}")

    async def _on_user_msg(self, frame: dict[str, Any]) -> None:
        if self._task is not None and not self._task.done():
            await self._send_error(_frame_id(frame), "Önceki istek hâlâ sürüyor.")
            return

        text = str(frame.get("text") or "").strip()
        request_id = str(frame.get("id") or "").strip() or _new_request_id()
        if not text:
            await self._send_error(request_id, "Boş mesaj gönderildi.")
            return

        source = str(frame.get("source") or "text")
        tab = frame.get("tab") if isinstance(frame.get("tab"), dict) else None
        self.bridge.set_request_id(request_id)
        self._task_request_id = request_id
        self._task = asyncio.create_task(
            self._run_request(request_id, text, source, tab), name=f"request-{request_id}"
        )
        logger.info("İstek başladı (request_id=%s, source=%s)", request_id, source)

    async def _on_cancel(self, frame: dict[str, Any]) -> None:
        request_id = str(_frame_id(frame) or self._task_request_id or "")
        task = self._task
        if task is not None and not task.done():
            if request_id and self._task_request_id and request_id != self._task_request_id:
                logger.info("cancel farklı istek için geldi: %s != %s", request_id, self._task_request_id)
            task.cancel()
        await self._announce_cancel(request_id or None)

    async def _announce_cancel(self, request_id: str | None) -> None:
        """Send the cancel error frame once per request id."""

        key = request_id or "-"
        if key in self._announced_cancels:
            return
        if len(self._announced_cancels) > 32:
            self._announced_cancels.clear()
        self._announced_cancels.add(key)
        await self._send_error(request_id, "İstek iptal edildi.")

    # -- request pipeline -------------------------------------------------
    async def _run_request(
        self,
        request_id: str,
        text: str,
        source: str,
        tab: dict[str, Any] | None,
    ) -> None:
        self._latest_snapshot = None
        # Task-local: agents add "Tarayıcı erişimi yok" to their context block.
        BROWSER_AVAILABLE.set(self.browser_available)
        emit = make_emitter(
            self._send, self.store, request_id, snapshot_provider=lambda: self._latest_snapshot
        )
        log = _RequestLogger(logger, {"request_id": request_id})
        history: list[HistoryEntry] = []
        snapshot: dict[str, Any] | None = None
        summary = ""
        evidence: list[str] = []
        revisions = 0
        calls_used = 0
        asked_user = False
        route = "chat"
        goal = text
        # The request budget is a *total*, so the deadline starts here — before
        # memory lookup, router and planner — and every later step is charged to
        # it (INTERFACES.md §10.3).  The last part of the budget is reserved for
        # the Responder: work stops at `work_deadline`, the answer is due by
        # `deadline`, so a slow model still yields a normal `final`.
        budget = float(self._settings.request_budget_s)
        started = time.monotonic()
        deadline = started + budget
        work_deadline = deadline - min(RESPONDER_RESERVE_S, budget * RESPONDER_RESERVE_RATIO)

        try:
            log.info("İstek işleniyor (source=%s, %d karakter)", source, len(text))

            if not bool(getattr(self.client, "configured", True)):
                # No key means every agent would answer from its fallback and the
                # user would get a fake "answer" instead of a diagnosable error.
                reason = "LLM yapılandırılmadı: DEEPSEEK_API_KEY tanımlı değil."
                log.warning(reason)
                await self._emit_quiet(emit, "system", "LLM yapılandırılmadı", reason)
                await self._send_error(request_id, reason)
                return

            router = self._require(self.router, "router")
            responder = self._require(self.responder, "responder")
            memories = await self._search_memories(
                text, request_id, tab, emit, deadline=work_deadline
            )
            decision = await _within(
                router.route(user_text=text, tab=tab, memories=memories, emit=emit), work_deadline
            )
            route = decision.route
            log.info("Route: %s (%.2f) — %s", route, decision.confidence, decision.why)
            if route == "browser_task" and not self.browser_available:
                # Deterministic: the desktop window has no browser to drive; the
                # Responder explains (its context block carries the no-browser note).
                route = "chat"
                log.info("browser_task -> chat (masaüstü penceresi, tarayıcı yok)")
                await self._emit_quiet(
                    emit,
                    "system",
                    "Tarayıcı erişimi yok, sohbet ile yanıtlanıyor",
                    "Masaüstü penceresinden tarayıcı kontrol edilemez; "
                    "tarayıcı görevleri Brave'deki Polaris panelinden yapılır.",
                )

            if route == "chat":
                final = await _within(
                    responder.respond(
                        goal=text,
                        route="chat",
                        result_summary="",
                        evidence=[],
                        snapshot=None,
                        history=[],
                        emit=emit,
                    ),
                    deadline,
                )
                await self._send_final(request_id, final)
                return

            planner = self._require(self.planner, "planner")
            executor = self._require(self.executor, "executor")
            judge = self._require(self.judge, "judge")

            plan = await _within(
                planner.plan(user_text=text, route=route, tab=tab, memories=memories, emit=emit),
                work_deadline,
            )
            goal = plan.goal or text
            last_revise_feedback: str | None = None

            while True:
                run = await self._run_tool_loop(
                    request_id=request_id,
                    executor=executor,
                    text=text,
                    tab=tab,
                    route=route,
                    plan=plan,
                    history=history,
                    snapshot=snapshot,
                    calls_used=calls_used,
                    deadline=work_deadline,
                    emit=emit,
                    log=log,
                )
                calls_used = run.calls
                snapshot = run.snapshot
                summary = run.summary or summary
                evidence = run.evidence or evidence

                if time.monotonic() >= work_deadline:
                    raise BudgetExceeded()
                verdict = await _within(
                    judge.judge(goal=goal, plan=plan, history=history, snapshot=snapshot, emit=emit),
                    work_deadline,
                )
                log.info("Judge: %s — %s", verdict.verdict, verdict.feedback)

                if verdict.verdict == "revise" and is_repeated_feedback(
                    last_revise_feedback, verdict.feedback
                ):
                    log.info("Judge aynı revizyonu tekrar istedi; revizyon durduruldu")
                    await self._emit_quiet(
                        emit,
                        "system",
                        "Revizyon durduruldu",
                        "Değerlendirici aynı düzeltmeyi tekrar istedi; eldeki sonuçla yanıtlanıyor.",
                    )
                    break

                if verdict.verdict == "revise" and revisions < int(self._settings.max_revisions):
                    last_revise_feedback = verdict.feedback
                    revisions += 1
                    history.append(
                        HistoryEntry(
                            tool="judge_feedback",
                            ok=True,
                            data=verdict.feedback,
                            why="Judge revizyon istedi",
                        )
                    )
                    log.info("Revizyon %d/%d", revisions, self._settings.max_revisions)
                    continue

                if verdict.verdict == "ask_user" and not asked_user:
                    asked_user = True
                    if not await self._ask_user_once(request_id, verdict.feedback, history, emit, log):
                        break
                    continue
                break

            final = await _within(
                responder.respond(
                    goal=goal,
                    route=route,
                    result_summary=summary,
                    evidence=evidence,
                    snapshot=snapshot,
                    history=history,
                    emit=emit,
                ),
                deadline,
            )
            await self._send_final(request_id, final)

        except BudgetExceeded:
            await self._finish_partial(
                request_id,
                goal=goal,
                route=route,
                summary=summary,
                evidence=evidence,
                snapshot=snapshot,
                history=history,
                emit=emit,
                deadline=deadline,
                elapsed_s=time.monotonic() - started,
                log=log,
            )
        except asyncio.CancelledError:
            log.info("İstek iptal edildi")
            await self._emit_quiet(emit, "system", "İstek iptal edildi", "Kullanıcı isteği durdurdu.")
            await self._announce_cancel(request_id)
            # Swallowed on purpose: the task must end cleanly after cancel().
        except Exception as exc:
            log.exception("İstek hata ile bitti: %s", exc)
            await self._emit_quiet(
                emit, "system", "İstek hata ile bitti", _error_text(exc)
            )
            await self._send_error(request_id, _error_text(exc))
        finally:
            if asyncio.current_task() is self._task:
                self._task = None
                self._task_request_id = None
            # `_announced_cancels` keeps the id: a repeated `cancel` frame for the
            # same request must not emit a second "İstek iptal edildi." frame.

    async def _finish_partial(
        self,
        request_id: str,
        *,
        goal: str,
        route: str,
        summary: str,
        evidence: list[str],
        snapshot: dict[str, Any] | None,
        history: list[HistoryEntry],
        emit: EmitFn,
        deadline: float,
        elapsed_s: float,
        log: logging.LoggerAdapter,  # type: ignore[type-arg]
    ) -> None:
        """Budget ran out: answer with what was gathered — never as a cancel."""

        budget = float(self._settings.request_budget_s)
        reason = f"İstek için ayrılan süre doldu ({budget:.0f} sn); eldeki sonuçla yanıtlanıyor."
        log.info("Bütçe doldu (%.1f sn), kısmi yanıt hazırlanıyor", elapsed_s)
        await self._emit_quiet(emit, "system", "Süre doldu, kısmi yanıt", reason)

        partial_summary = f"{PARTIAL_PREFIX} {summary}".strip() if summary else PARTIAL_PREFIX
        final: FinalAnswer | None = None
        responder = self.responder
        # The Responder normally has its reserved slice; if even that is gone it
        # still gets a short grace period rather than no answer at all.
        responder_deadline = max(deadline, time.monotonic() + 10.0)
        if responder is not None:
            try:
                final = await _within(
                    responder.respond(
                        goal=goal,
                        route=route,
                        result_summary=partial_summary,
                        evidence=evidence,
                        snapshot=snapshot,
                        history=history,
                        emit=emit,
                    ),
                    responder_deadline,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # BudgetExceeded included
                log.info("Kısmi yanıt için Responder başarısız: %s", _error_text(exc))
                final = None

        if final is None or not (final.answer or "").strip():
            body = summary.strip() or _history_digest(history) or "Henüz kullanılabilir bir sonuç toplanamadı."
            final = FinalAnswer(
                answer=f"{PARTIAL_PREFIX}\n\n{body}",
                spoken="Süre doldu; eldeki sonucu yazdım.",
            )
        elif not final.answer.lstrip().startswith(PARTIAL_PREFIX):
            final = final.model_copy(update={"answer": f"{PARTIAL_PREFIX}\n\n{final.answer}"})
        await self._send_final(request_id, final)

    async def _run_tool_loop(
        self,
        *,
        request_id: str,
        executor: ExecutorAgent,
        text: str,
        tab: dict[str, Any] | None,
        route: str,
        plan: Plan,
        history: list[HistoryEntry],
        snapshot: dict[str, Any] | None,
        calls_used: int,
        deadline: float,
        emit: EmitFn,
        log: logging.LoggerAdapter,  # type: ignore[type-arg]
    ) -> _ToolRun:
        """Executor turns until `finish`, a limit or the budget runs out."""

        tools = registry.list_for(route)
        if not self.browser_available:
            tools = without_browser_tools(tools)
        goal = plan.goal or text
        tool_calls = calls_used
        if snapshot is not None:
            self._latest_snapshot = snapshot
        summary = ""
        evidence: list[str] = []

        while True:
            reason = self._budget_reason(deadline, tool_calls)
            if reason:
                log.info("Döngü durdu: %s", reason)
                await self._emit_quiet(emit, "system", "Bütçe/limit doldu", reason)
                break

            # The executor's own LLM turn is capped by what is left of the
            # request budget, so a slow model cannot overshoot it silently.
            budget_left = max(0.0, deadline - time.monotonic())
            try:
                action = await asyncio.wait_for(
                    executor.step(
                        goal=goal,
                        plan=plan,
                        route=route,
                        history=history,
                        snapshot=snapshot,
                        tools=tools,
                        emit=emit,
                    ),
                    timeout=budget_left,
                )
            except asyncio.TimeoutError:
                reason = "İstek için ayrılan toplam süre doldu (executor turu)."
                log.info("Executor turu bütçeyi aştı: %s", reason)
                await self._emit_quiet(emit, "system", "Bütçe/limit doldu", reason)
                break

            if action.kind == "finish":
                summary = action.summary
                evidence = list(action.evidence)
                log.info("Executor bitti: %s", summary[:160])
                break

            tool = registry.get(action.tool or "")
            if tool is not None and not self.browser_available and tool.name.startswith("browser."):
                tool_calls += 1
                log.info("Masaüstü penceresi: %s çalıştırılmadı", tool.name)
                history.append(
                    HistoryEntry(
                        tool=tool.name,
                        args=action.args,
                        ok=False,
                        error=NO_BROWSER_ERROR,
                        why=action.why,
                    )
                )
                continue
            if tool is None:
                # Counted as a turn: a model that keeps naming an unknown tool
                # must hit `max_tool_calls` instead of looping to the deadline.
                tool_calls += 1
                error = f"Bilinmeyen araç: {action.tool or '?'}"
                log.info(error)
                history.append(
                    HistoryEntry(
                        tool=action.tool or "?",
                        args=action.args,
                        ok=False,
                        error=error,
                        why=action.why,
                    )
                )
                continue

            # Schema check before any gate or handler: a malformed call is sent
            # back to the executor as a Turkish error it can correct.
            invalid = registry.validate_args(tool, action.args)
            if invalid:
                tool_calls += 1
                log.info("Geçersiz argüman (%s): %s", tool.name, invalid)
                history.append(
                    HistoryEntry(
                        tool=tool.name, args=action.args, ok=False, error=invalid, why=action.why
                    )
                )
                await self._emit_quiet(
                    emit,
                    "system",
                    "Araç argümanları geçersiz, çalıştırılmadı",
                    invalid,
                    tool_name=tool.name,
                    tool_args=action.args,
                )
                continue

            blocked = registry.blocked_reason(tool, action.args, snapshot)
            if blocked:
                tool_calls += 1
                log.info("Araç engellendi (%s): %s", tool.name, blocked)
                history.append(
                    HistoryEntry(
                        tool=tool.name, args=action.args, ok=False, error=blocked, why=action.why
                    )
                )
                await self._emit_quiet(
                    emit,
                    "system",
                    "Güvenlik: işlem engellendi",
                    blocked,
                    tool_name=tool.name,
                    tool_args=action.args,
                )
                continue

            # Deterministic confirmation gate: it always runs before the handler.
            confirm_summary = registry.needs_confirm(tool, action.args, snapshot)
            if confirm_summary:
                confirm_call_id = new_call_id()
                await emit(
                    make_step(
                        "judge",
                        "Kullanıcı onay istendi",
                        confirm_summary,
                        tool={"name": tool.name, "args": action.args},
                        verdict="ask_user",
                    )
                )
                approved = await self.bridge.request_confirm(
                    confirm_call_id,
                    confirm_summary,
                    tool.name,
                    action.args,
                    timeout=float(self._settings.confirm_timeout_s),
                )
                if not approved:
                    log.info("Kullanıcı reddetti: %s", tool.name)
                    history.append(
                        HistoryEntry(
                            tool=tool.name,
                            args=action.args,
                            ok=False,
                            error="kullanıcı reddetti",
                            why=action.why,
                            rejected=True,
                        )
                    )
                    tool_calls += 1
                    await self._emit_quiet(
                        emit,
                        "executor",
                        "Kullanıcı reddetti, araç çalıştırılmadı",
                        confirm_summary,
                        tool_name=tool.name,
                        tool_args=action.args,
                    )
                    continue

            tool_calls += 1
            ctx = ToolContext(
                request_id=request_id,
                bridge=self.bridge,
                tab=tab,
                emit_trace=emit,
                extras={},
            )
            result = await self._invoke_tool(tool, action.args, ctx, deadline=deadline)
            if result.get("ok"):
                data = result.get("data")
                if isinstance(data, dict) and "elements" in data:
                    snapshot = data
                    self._latest_snapshot = data
                history.append(
                    HistoryEntry(
                        tool=tool.name, args=action.args, ok=True, data=data, why=action.why
                    )
                )
            else:
                error_text = str(result.get("error") or "bilinmeyen hata")
                log.info("Araç hatası (%s): %s", tool.name, error_text)
                # The page re-rendered (e.g. a search box swapped for a live
                # typeahead after typing): drop the dead ref from the cached
                # snapshot so the executor's stale-ref guard forces a fresh
                # browser.snapshot instead of retrying the same ref forever.
                if "ref bulunamadı" in error_text and isinstance(snapshot, dict):
                    dead = str(action.args.get("ref"))
                    elements = snapshot.get("elements")
                    if isinstance(elements, list):
                        snapshot = {
                            **snapshot,
                            "elements": [
                                el for el in elements
                                if not (isinstance(el, dict) and str(el.get("ref")) == dead)
                            ],
                        }
                        self._latest_snapshot = snapshot
                history.append(
                    HistoryEntry(
                        tool=tool.name,
                        args=action.args,
                        ok=False,
                        error=error_text,
                        why=action.why,
                    )
                )
                await self._emit_quiet(
                    emit,
                    "executor",
                    f"Araç hatası: {error_text}",
                    error_text,
                    tool_name=tool.name,
                    tool_args=action.args,
                )

        return _ToolRun(calls=tool_calls, summary=summary, evidence=evidence, snapshot=snapshot)

    def _budget_reason(self, deadline: float, tool_calls: int) -> str | None:
        """Why the executor loop must stop, or `None` while it may continue.

        The budget is measured against one absolute deadline for the whole
        request, so router/planner latency and earlier revisions are included.
        """

        settings = self._settings
        if tool_calls >= int(settings.max_tool_calls):
            return f"Araç çağrısı sınırı doldu ({tool_calls}/{settings.max_tool_calls})."
        if deadline - time.monotonic() <= 0:
            return f"İstek bütçesi doldu ({settings.request_budget_s:.0f} sn)."
        return None

    async def _ask_user_once(
        self,
        request_id: str,
        feedback: str,
        history: list[HistoryEntry],
        emit: EmitFn,
        log: logging.LoggerAdapter,  # type: ignore[type-arg]
    ) -> bool:
        """Ask the user once; the answer is appended to the history."""

        summary = f"Judge ek bilgi istiyor: {feedback or 'Devam edilsin mi?'}"
        call_id = new_call_id()
        await emit(make_step("judge", "Kullanıcıdan ek bilgi istendi", summary, verdict="ask_user"))
        approved = await self.bridge.request_confirm(
            call_id, summary, "judge.ask_user", {}, timeout=JUDGE_QUESTION_TIMEOUT_S
        )
        log.info("Judge sorusuna cevap: %s", "evet" if approved else "hayır")
        history.append(
            HistoryEntry(
                tool="kullanici_cevabi",
                args={},
                ok=approved,
                data="kullanıcı devam etmeyi onayladı" if approved else None,
                error=None if approved else "kullanıcı reddetti",
                why="Judge ek bilgi istedi",
                rejected=not approved,
            )
        )
        return approved

    async def _search_memories(
        self,
        text: str,
        request_id: str,
        tab: dict[str, Any] | None,
        emit: EmitFn,
        *,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Best-effort memory lookup; the tool is optional (owner: K5)."""

        tool = registry.get("memory.search")
        if tool is None:
            return []
        ctx = ToolContext(
            request_id=request_id, bridge=self.bridge, tab=tab, emit_trace=emit, extras={}
        )
        result = await self._invoke_tool(tool, {"query": text, "limit": 3}, ctx, deadline=deadline)
        if not result.get("ok"):
            logger.info("memory.search başarısız (%s): %s", request_id, result.get("error"))
            return []
        return _as_memories(result.get("data"))

    async def _invoke_tool(
        self,
        tool: Tool,
        args: dict[str, Any],
        ctx: ToolContext,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Run a handler with a hard timeout; never raises.

        When a request deadline is given, the handler never runs longer than the
        budget that is left, so the total request time stays bounded even with a
        slow bridge (`tool_timeout_s` + margin is only the upper bound).
        """

        timeout = float(self._settings.tool_timeout_s) + TOOL_TIMEOUT_MARGIN_S
        if deadline is not None:
            budget_left = deadline - time.monotonic()
            if budget_left <= 0:
                logger.info("Araç çalıştırılmadı, bütçe doldu (%s)", tool.name)
                return {"ok": False, "error": f"istek bütçesi doldu, araç çalıştırılmadı: {tool.name}"}
            timeout = min(timeout, budget_left)
        try:
            result = await asyncio.wait_for(tool.handler(args, ctx), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info("Araç zaman aşımı (%s): %.0f sn", tool.name, timeout)
            return {"ok": False, "error": f"araç zaman aşımı: {tool.name} ({timeout:.0f} sn)"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Araç istisna fırlattı (%s): %s", tool.name, exc)
            return {"ok": False, "error": f"araç hatası: {exc}"}
        if not isinstance(result, dict):
            return {"ok": False, "error": "araç beklenmeyen bir sonuç döndürdü"}
        return _normalise_result(tool, result)

    # -- sending ----------------------------------------------------------
    async def _send_error(self, request_id: str | None, message: str) -> None:
        try:
            await self._send({"type": "error", "request_id": request_id, "message": message})
        except Exception as exc:
            logger.info("error frame gönderilemedi: %s", exc)

    async def _send_final(self, request_id: str, final: FinalAnswer) -> None:
        try:
            await self._send(
                {
                    "type": "final",
                    "request_id": request_id,
                    "answer": final.answer,
                    "spoken": final.spoken_text(),
                    "citations": [citation.model_dump() for citation in final.citations],
                }
            )
        except Exception as exc:
            logger.info("final frame gönderilemedi (%s): %s", request_id, exc)

    @staticmethod
    async def _emit_quiet(
        emit: EmitFn,
        agent: str,
        decision: str,
        why: str,
        *,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        """Trace a system/action step; tracing failures never break a request."""

        try:
            await emit(
                make_step(
                    agent,  # type: ignore[arg-type]
                    decision,
                    why,
                    tool={"name": tool_name, "args": tool_args or {}} if tool_name else None,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("Trace yayılamadı: %s", exc)


def _history_digest(history: list[HistoryEntry], limit: int = 5) -> str:
    """Short markdown list of the last successful tool steps (partial answers)."""

    lines: list[str] = []
    for entry in history:
        if not entry.ok or entry.tool in {"judge_feedback", "kullanici_cevabi"}:
            continue
        data = entry.data
        if isinstance(data, dict):
            text = str(data.get("text") or data.get("title") or "").strip()
        else:
            text = str(data or "").strip()
        if text:
            lines.append(f"- `{entry.tool}`: {text[:300]}")
    return "\n".join(lines[-limit:])


def _frame_id(frame: dict[str, Any]) -> str | None:
    raw = frame.get("id") or frame.get("request_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


async def serve_connection(websocket: WebSocket) -> None:
    """WS reader loop: never crashes on a bad frame or a broken request."""

    await websocket.accept()
    bridge = BrowserBridge(websocket.send_json)
    orch = Orchestrator(websocket.send_json, bridge=bridge)
    try:
        await orch.start()
    except Exception as exc:
        logger.exception("Orchestrator başlatılamadı: %s", exc)
        try:
            await websocket.send_json(
                {"type": "error", "request_id": None, "message": f"Sunucu hazırlanamadı: {exc}"}
            )
        except Exception:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except ValueError:
                await websocket.send_json(
                    {"type": "error", "request_id": None, "message": "Geçersiz JSON"}
                )
                continue
            if not isinstance(frame, dict):
                await websocket.send_json(
                    {"type": "error", "request_id": None, "message": "Geçersiz frame."}
                )
                continue
            try:
                await orch.handle_frame(frame)
            except Exception as exc:  # one bad frame must not kill the socket
                logger.exception("Frame işlenemedi: %s", exc)
                await websocket.send_json(
                    {"type": "error", "request_id": _frame_id(frame), "message": _error_text(exc)}
                )
    except WebSocketDisconnect:
        logger.info("İstemci bağlantıyı kapattı")
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("WS okuma döngüsü bitti: %s", exc)
    finally:
        await orch.close()
        await bridge.close()
