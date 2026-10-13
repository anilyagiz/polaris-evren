"""E2E scenarios.  Each one drives the REAL backend through the fake extension.

A scenario collects every failed expectation (via `ctx.check`) instead of
stopping at the first one, so a single run gives a complete bug list.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .fake_extension import FakeExtension, FakePage, Run
from .harness import BackendProc, Stack


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Ctx:
    def __init__(self, stack: Stack, backend_name: str) -> None:
        self.stack = stack
        self.backend: BackendProc = stack.backend(backend_name)
        self.llm = stack.fake.state
        self.failures: list[str] = []
        self.notes: list[str] = []
        self._exts: list[FakeExtension] = []
        self.llm.reset()

    # -- helpers ----------------------------------------------------------
    def check(self, cond: Any, message: str) -> bool:
        if not cond:
            self.failures.append(message)
        return bool(cond)

    def note(self, message: str) -> None:
        self.notes.append(message)

    async def ext(self, **kwargs: Any) -> FakeExtension:
        ext = FakeExtension(self.backend.ws, **kwargs)
        await ext.connect()
        self._exts.append(ext)
        return ext

    async def finish(self) -> None:
        for ext in self._exts:
            await ext.settle(0.1)
            for problem in ext.violations:
                self.failures.append(f"PROTOCOL SCHEMA: {problem}")
            await ext.close()
        if not self.backend.alive():
            self.failures.append("backend process died during the scenario")

    def dump(self, run: Run) -> None:
        self.notes.append("events:\n    " + run.summary().replace("\n", "\n    "))

    # -- common assertions ------------------------------------------------
    def expect_final(self, run: Run, label: str = "") -> dict[str, Any] | None:
        tag = f"{label}: " if label else ""
        if run.timed_out:
            self.failures.append(f"{tag}no final/error within timeout")
            self.dump(run)
            return None
        if run.final is None:
            self.failures.append(f"{tag}expected `final`, got error: {run.errors[:1]}")
            self.dump(run)
            return None
        self.check(
            all(e.get("request_id") == run.request_id for e in run.events if e["type"] in ("trace", "tool_call", "need_confirm", "final")),
            f"{tag}some events carry a request_id different from {run.request_id}",
        )
        return run.final

    def route_of(self, run: Run) -> str | None:
        for s in run.traces:
            if s["agent"] == "router":
                m = re.match(r"Route: (\w+)", s["decision"])
                return m.group(1) if m else None
        return None


ScenarioFn = Callable[[Ctx], Awaitable[None]]
SCENARIOS: dict[str, tuple[str, str, ScenarioFn]] = {}


def scenario(name: str, backend: str = "main", doc: str = "") -> Callable[[ScenarioFn], ScenarioFn]:
    def deco(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS[name] = (backend, doc or (fn.__doc__ or "").strip(), fn)
        return fn

    return deco


# ==========================================================================
# 0. infrastructure sanity
# ==========================================================================
@scenario("health_http", doc="GET /health and CORS preflight")
async def health_http(ctx: Ctx) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ctx.backend.http}/health")
        ctx.check(r.status_code == 200 and r.json().get("ok") is True, f"/health bad: {r.status_code} {r.text[:100]}")
        ext_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
        r = await client.options(
            f"{ctx.backend.http}/health",
            headers={"Origin": ext_origin, "Access-Control-Request-Method": "GET"},
        )
        ctx.check(r.status_code == 200 and r.headers.get("access-control-allow-origin") in ("*", ext_origin), f"CORS preflight for a chrome-extension origin failed: {r.status_code}")
        r = await client.get(f"{ctx.backend.http}/health", headers={"Origin": "http://localhost:5173"})
        ctx.check(r.headers.get("access-control-allow-origin") in ("*", "http://localhost:5173"), "CORS does not allow the localhost dev origin")
        r = await client.get(f"{ctx.backend.http}/health", headers={"Origin": "https://evil.example.com"})
        if r.headers.get("access-control-allow-origin") == "*":
            ctx.note("CORS allows every origin (PROTOCOL.md section 5 says so; fine for the MVP)")


# ==========================================================================
# 1. chat
# ==========================================================================
@scenario("chat_greeting", doc="greeting -> router+responder only (no planner/executor/judge), final shape")
async def chat_greeting(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Merhaba, nasılsın?", tab=None)
    final = ctx.expect_final(run)
    ctx.check(ctx.route_of(run) == "chat", f"route should be chat, got {ctx.route_of(run)}")
    agents = run.agents()
    ctx.check(agents == ["router", "responder"], f"chat trace agents should be [router, responder], got {agents}")
    for banned in ("planner", "executor", "judge"):
        ctx.check(banned not in agents, f"chat produced a {banned} trace")
        ctx.check(not ctx.llm.calls_for(banned), f"chat triggered an LLM call for {banned}")
    ctx.check(not run.tool_calls, "chat must not issue tool_calls")
    if final:
        ctx.check("Merhaba" in final["answer"], f"unexpected answer: {final['answer'][:80]}")
    ctx.check(all(isinstance(s["latency_ms"], int) for s in run.traces), "latency_ms must be int")
    ctx.check(any(s["tokens"] for s in run.traces), "no trace carries token usage (tokens null everywhere)")
    # the request is persisted for the Why timeline
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ctx.backend.http}/traces/{run.request_id}")
        steps = r.json().get("steps", []) if r.status_code == 200 else []
        ctx.check(len(steps) >= 2, f"/traces/{run.request_id} returned {len(steps)} steps (SQLite persistence)")


@scenario("chat_voice_source", doc="source=voice user_msg is accepted and answered")
async def chat_voice_source(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Merhaba", source="voice", tab=None)
    ctx.expect_final(run)


# ==========================================================================
# 2. browser.read page summary
# ==========================================================================
@scenario("page_summary", doc="'Bu sayfayı özetle' -> browser.read -> summary + citations/spoken")
async def page_summary(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Bu sayfayı özetle")
    final = ctx.expect_final(run)
    ctx.check(ctx.route_of(run) == "browser_task", f"route should be browser_task, got {ctx.route_of(run)}")
    names = [c["name"] for c in run.tool_calls]
    ctx.check("browser.read" in names, f"browser.read not requested; tool_calls={names}")
    ctx.check(run.agents()[:3] == ["router", "planner", "executor"], f"trace order: {run.agents()}")
    ctx.check("judge" in run.agents(), "no judge trace")
    ctx.check(run.agents()[-1] == "responder", f"last trace should be responder, got {run.agents()[-1:]}")
    exec_tools = [s["tool"]["name"] for s in run.traces if s["agent"] == "executor" and s["tool"]]
    ctx.check("browser.read" in exec_tools, f"executor trace lacks tool=browser.read ({exec_tools})")
    judges = [s for s in run.traces if s["agent"] == "judge"]
    ctx.check(judges and judges[-1]["verdict"] == "pass" and judges[-1]["scores"], "judge trace must carry scores + verdict=pass")
    if final:
        ctx.check("alışveriş" in final["answer"].lower(), f"final answer lost the executor summary: {final['answer'][:100]}")
    # LLM saw the tab context in its system prompt
    router_calls = ctx.llm.calls_for("router")
    ctx.check(router_calls and "shop.example.com" in router_calls[0].system, "tab url missing from router system prompt")
    # page text reached the executor's next turn (tool result -> HISTORY)
    execs = ctx.llm.calls_for("executor")
    ctx.check(len(execs) >= 2 and "Klavye Dünyası" in execs[-1].user, "browser.read result did not reach the executor HISTORY block")


# ==========================================================================
# 3. web search
# ==========================================================================
@scenario("web_search", doc="web_search route -> web.search (fake Brave) -> sourced answer")
async def web_search(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Brave'de en iyi 3 kablosuz kulaklığı ara, karşılaştır")
    final = ctx.expect_final(run)
    ctx.check(ctx.route_of(run) == "web_search", f"route should be web_search, got {ctx.route_of(run)}")
    ctx.check(not run.tool_calls, f"web.search must run in the backend, but the extension got {[c['name'] for c in run.tool_calls]}")
    ctx.check(ctx.llm.brave_queries, "fake Brave server never queried (web.search handler failed?)")
    exec_tools = [s["tool"]["name"] for s in run.traces if s["agent"] == "executor" and s["tool"]]
    ctx.check("web.search" in exec_tools, f"executor trace lacks web.search ({exec_tools})")
    execs = ctx.llm.calls_for("executor")
    ctx.check(
        len(execs) >= 2 and "reviews.example.com" in execs[-1].user,
        "web.search results never reached the executor HISTORY block (handler returns {ok, results} without `data`; orchestrator reads result['data'])",
    )
    if final:
        ctx.check(len(final["citations"]) >= 1, "final.citations is empty for a web search answer")
        ctx.check(all(c["url"].startswith("https://reviews.example.com") for c in final["citations"]), f"unexpected citations {final['citations']}")
    if not run.timed_out and ctx.failures:
        ctx.dump(run)


# ==========================================================================
# 4. browser search task with highlight evidence
# ==========================================================================
@scenario("browser_search_highlight", doc="snapshot -> type -> read -> highlight; evidence refs in trace")
async def browser_search_highlight(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Bu sitede mekanik klavye ara, ilk 3 fiyatı söyle")
    final = ctx.expect_final(run)
    names = [c["name"] for c in run.tool_calls]
    ctx.check(names[:4] == ["browser.snapshot", "browser.type", "browser.read", "browser.highlight"], f"tool order: {names}")
    typed = [c for c in run.tool_calls if c["name"] == "browser.type"]
    ctx.check(typed and typed[0]["args"] == {"ref": 7, "text": "mekanik klavye"}, f"browser.type args must be clean (no `why`), got {typed[0]['args'] if typed else None}")
    ctx.check(ext.page.values.get(7) == "mekanik klavye", "search box ref 7 never received the text")
    ctx.check(ext.page.highlighted == [21], f"highlight ref mismatch: {ext.page.highlighted}")
    ctx.check(not run.confirms, "plain typing/highlight must not ask for confirmation")
    refs = [e for s in run.traces for e in s["evidence"] if e.startswith("ref:")]
    ctx.check(refs, "no trace carries `ref:` evidence (panel highlight-on-click needs it): executor never fills TraceStep.evidence and finish `evidence` is not propagated")
    if final:
        ctx.check("1.499" in final["answer"], f"prices missing from final: {final['answer'][:100]}")
    if ctx.failures:
        ctx.dump(run)


# ==========================================================================
# 5. risky click gate
# ==========================================================================
@scenario("risky_click_deny", doc="click 'Siparişi tamamla' -> need_confirm -> deny => no browser.click")
async def risky_click_deny(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="deny")
    run = await ext.ask("Siparişi tamamla")
    ctx.expect_final(run)
    ctx.check(len(run.confirms) == 1, f"expected exactly 1 need_confirm, got {len(run.confirms)}")
    if run.confirms:
        c = run.confirms[0]
        ctx.check(c["tool"] == "browser.click" and c["args"] == {"ref": 52}, f"need_confirm payload: {c['tool']} {c['args']}")
        ctx.check("Siparişi tamamla" in c["summary"], f"summary should name the button: {c['summary']}")
    ctx.check("browser.click" not in [c["name"] for c in run.tool_calls], "browser.click was sent despite denial")
    ctx.check(ext.page.executed == [], f"page side effects after denial: {ext.page.executed}")
    asks = [s for s in run.traces if s["agent"] == "judge" and s["verdict"] == "ask_user"]
    ctx.check(asks, "no judge trace with verdict=ask_user for the confirmation")
    ctx.check(any("reddet" in s["decision"].lower() for s in run.traces), "no trace explaining the user rejection")
    ctx.check(ctx.llm.calls_for("executor") and "kullanıcı reddetti" in ctx.llm.calls_for("executor")[-1].user, "rejection not visible to executor HISTORY")


@scenario("risky_click_approve", doc="same click, approve => browser.click executed exactly once")
async def risky_click_approve(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="approve")
    run = await ext.ask("Siparişi tamamla")
    ctx.expect_final(run)
    ctx.check(len(run.confirms) == 1, f"expected exactly 1 need_confirm, got {len(run.confirms)}")
    clicks = [c for c in run.tool_calls if c["name"] == "browser.click"]
    ctx.check(len(clicks) == 1 and clicks[0]["args"] == {"ref": 52}, f"browser.click calls: {clicks}")
    ctx.check(ext.page.executed_names() == ["browser.click"], f"page executed: {ext.page.executed}")
    if run.confirms and clicks:
        ci = run.events.index(run.confirms[0])
        ck = run.events.index(clicks[0])
        ctx.check(ci < ck, "need_confirm must precede the click tool_call")


@scenario("risky_click_confirm_timeout", doc="no answer to need_confirm => treated as denied (CONFIRM_TIMEOUT_S=3)")
async def risky_click_confirm_timeout(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="silent")
    run = await ext.ask("Siparişi tamamla", timeout=30)
    ctx.expect_final(run)
    ctx.check(len(run.confirms) == 1, f"expected 1 need_confirm, got {len(run.confirms)}")
    ctx.check(ext.page.executed == [], f"click executed without consent: {ext.page.executed}")
    ctx.check(run.elapsed_s >= 2.5, f"returned in {run.elapsed_s:.1f}s - did it really wait for the confirm timeout?")


@scenario("late_confirm_ignored", doc="confirm for an unknown/old call_id is ignored, connection survives")
async def late_confirm_ignored(ctx: Ctx) -> None:
    ext = await ctx.ext()
    await ext.send({"type": "confirm", "call_id": "c_nope", "approved": True})
    await ext.send({"type": "tool_result", "call_id": "c_nope", "ok": True, "data": {}, "error": None})
    run = await ext.ask("Merhaba", tab=None)
    ctx.expect_final(run)


@scenario("confirm_string_false_fails_closed", doc='confirm.approved="false" (string) must NOT approve a risky click')
async def confirm_string_false(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="silent")

    async def answer(event: dict[str, Any]) -> None:
        await ext.send({"type": "confirm", "call_id": event["call_id"], "approved": "false"})

    ext.confirm_policy = lambda e: (asyncio.create_task(answer(e)), None)[1]
    run = await ext.ask("Siparişi tamamla", timeout=30)
    ctx.expect_final(run)
    ctx.check(ext.page.executed == [], f"string 'false' approved a risky click: {ext.page.executed}")


# ==========================================================================
# 6. form fill + submit
# ==========================================================================
@scenario("form_submit_deny", doc="fill fields (no confirm) -> submit=true needs confirm -> deny => submit not executed")
async def form_submit_deny(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="deny")
    run = await ext.ask("Bu formu doldur ve gönder")
    ctx.expect_final(run)
    ctx.check(len(run.confirms) == 1, f"exactly one confirmation (the submit) expected, got {len(run.confirms)}")
    if run.confirms:
        c = run.confirms[0]
        ctx.check(c["tool"] == "browser.type" and c["args"].get("submit") is True, f"confirm payload {c['tool']} {c['args']}")
    types = [c for c in run.tool_calls if c["name"] == "browser.type"]
    ctx.check(len(types) == 1 and types[0]["args"].get("ref") == 5, f"only the non-submitting fill should reach the extension: {types}")
    ctx.check(not any(a.get("submit") for _, a in ext.page.executed), "submit=true executed after denial")
    ctx.check(ext.page.values.get(5) == "Ayşe Yılmaz", "first field not filled")


@scenario("form_submit_approve", doc="approve => browser.type submit=true reaches the extension as a real boolean")
async def form_submit_approve(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="approve")
    run = await ext.ask("Bu formu doldur ve gönder")
    ctx.expect_final(run)
    types = [c for c in run.tool_calls if c["name"] == "browser.type"]
    ctx.check(len(types) == 2, f"expected 2 browser.type calls, got {len(types)}")
    if len(types) == 2:
        ctx.check(types[1]["args"].get("submit") is True, f"submit must be boolean true: {types[1]['args']}")
        ctx.check("why" not in types[0]["args"] and "why" not in types[1]["args"], "executor leaked `why` into tool args")


# ==========================================================================
# 7. reminders
# ==========================================================================
@scenario("reminder_notify", doc="reminders.add then `notify` frame when due (poll 5 s)")
async def reminder_notify(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Yarın saat 10'da toplantıyı hatırlat", tab=None)
    ctx.expect_final(run)
    ctx.check(ctx.route_of(run) == "personal", f"route should be personal, got {ctx.route_of(run)}")
    ctx.check(not run.tool_calls, "reminders.add must not go through the extension")
    exec_tools = [s["tool"]["name"] for s in run.traces if s["agent"] == "executor" and s["tool"]]
    ctx.check("reminders.add" in exec_tools, f"executor trace lacks reminders.add ({exec_tools})")
    execs = ctx.llm.calls_for("executor")
    ctx.check(execs and "HATA" not in execs[-1].user, f"reminders.add failed: {execs[-1].user[-600:] if execs else ''}")
    note = await ext.wait_for(lambda e: e.get("type") == "notify", timeout=25)
    ctx.check(note is not None, "no `notify` frame within 25 s of the due time")
    if note:
        ctx.check("toplantı" in note["text"], f"notify text: {note['text']}")
    # delivered exactly once
    await asyncio.sleep(6)
    ctx.check(len(ext.notifications) == 1, f"notify delivered {len(ext.notifications)} times")


# ==========================================================================
# 8. memory
# ==========================================================================
@scenario("memory_save_search", doc="memory.save then memory.search (+ prefetch into router context)")
async def memory_save_search(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Kedimin adı Pamuk, bunu unutma", tab=None)
    ctx.expect_final(run)
    exec_tools = [s["tool"]["name"] for s in run.traces if s["agent"] == "executor" and s["tool"]]
    ctx.check("memory.save" in exec_tools, f"executor trace lacks memory.save ({exec_tools})")
    ctx.llm.reset()
    run2 = await ext.ask("Kedimin adı ne?", tab=None)
    final = ctx.expect_final(run2, "search")
    exec_tools2 = [s["tool"]["name"] for s in run2.traces if s["agent"] == "executor" and s["tool"]]
    ctx.check("memory.search" in exec_tools2, f"executor trace lacks memory.search ({exec_tools2})")
    routers = ctx.llm.calls_for("router")
    ctx.check(
        routers and "Pamuk" in routers[0].system,
        "memory prefetch: saved memory not injected into the router context (orchestrator reads result['data'], memory.search returns {ok, items})",
    )
    execs = ctx.llm.calls_for("executor")
    ctx.check(execs and any("Pamuk" in c.user for c in execs[1:]), "memory.search result never reached the executor HISTORY block")
    if final:
        ctx.check("Pamuk" in final["answer"], f"final answer does not contain the remembered name: {final['answer'][:100]}")
    if ctx.failures:
        ctx.dump(run2)


# ==========================================================================
# 9. cancel / timeouts / disconnects
# ==========================================================================
@scenario("cancel_mid_request", doc="cancel while a tool_call is pending => error 'iptal', no final, next request works")
async def cancel_mid_request(ctx: Ctx) -> None:
    ext = await ctx.ext(tools="silent")
    rid, idx = await ext.start("Yavaş yavaş bu sayfayı özetle")
    call = await ext.wait_for(lambda e: e.get("type") == "tool_call", timeout=20, since=idx)
    ctx.check(call is not None, "no tool_call before cancel")
    await ext.send({"type": "cancel", "id": rid})
    err = await ext.wait_for(lambda e: e.get("type") == "error" and e.get("request_id") == rid, timeout=10, since=idx)
    ctx.check(err is not None, "no error frame after cancel")
    if err:
        ctx.check("iptal" in err["message"].lower(), f"cancel error message: {err['message']}")
    await ext.settle(1.0)
    ctx.check(not any(e["type"] == "final" and e.get("request_id") == rid for e in ext.events), "final arrived after cancel")
    ctx.check(sum(1 for e in ext.events if e["type"] == "error" and e.get("request_id") == rid) == 1, "cancel produced more than one error frame")
    # connection and single-request slot are free again
    ext.tools_policy = "answer"
    run = await ext.ask("Merhaba", tab=None, timeout=20)
    ctx.expect_final(run, "after cancel")


@scenario("tool_result_timeout", doc="extension never answers tool_call => timeout (TOOL_TIMEOUT_S=3) handled, request still ends")
async def tool_result_timeout(ctx: Ctx) -> None:
    ext = await ctx.ext(tools="silent")
    run = await ext.ask("Sayfayı özetle (zaman aşımı testi)", timeout=40)
    ctx.check(not run.timed_out, "request never ended after a tool_result timeout")
    ctx.check(run.final is not None or run.errors, "neither final nor error")
    ctx.check(2.5 <= run.elapsed_s <= 30, f"timeout handled in {run.elapsed_s:.1f}s (expected ~3s)")
    ctx.check(
        any("zaman aşımı" in s["decision"].lower() or "zaman aşımı" in s["why"].lower() for s in run.traces),
        "no trace explains the tool timeout",
    )
    # a late tool_result must be ignored, not crash anything
    if run.tool_calls:
        await ext.send({"type": "tool_result", "call_id": run.tool_calls[0]["call_id"], "ok": True, "data": {"text": "geç"}, "error": None})
    run2 = await ext.ask("Merhaba", tab=None, timeout=20)
    ctx.expect_final(run2, "after timeout")


@scenario("extension_disconnect_mid_request", doc="socket dies while a tool_call is pending => backend survives, new connection works")
async def extension_disconnect(ctx: Ctx) -> None:
    ext = await ctx.ext(tools="disconnect")
    rid, idx = await ext.start("Bağlantı kopsun, sayfayı özetle")
    await ext.wait_for(lambda e: e.get("type") == "tool_call", timeout=20, since=idx)
    await asyncio.sleep(1.5)
    ctx.check(ctx.backend.alive(), "backend process died when the extension disconnected")
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ctx.backend.http}/health")
        ctx.check(r.status_code == 200, "/health failed after disconnect")
    log = ctx.backend.log_text()
    ext2 = await ctx.ext()
    run = await ext2.ask("Merhaba", tab=None, timeout=20)
    ctx.expect_final(run, "new connection")
    ctx.check(
        not re.search(r"Task exception was never retrieved|RuntimeError: Cannot call \"send\"", log),
        "backend logged an unretrieved task exception / send-after-close after the disconnect",
    )


# ==========================================================================
# 10. limits and judge loops
# ==========================================================================
@scenario("tool_call_cap", doc="runaway executor stops at 8 tool calls with a system trace, still answers")
async def tool_call_cap(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Sonsuz kaydır", timeout=60)
    ctx.expect_final(run)
    ctx.check(len(run.tool_calls) == 8, f"expected exactly 8 tool_calls (max_tool_calls), got {len(run.tool_calls)}")
    ctx.check(any(s["agent"] == "system" for s in run.traces), "no system trace explaining the limit")


@scenario("judge_revise", doc="judge verdict=revise => executor retried, max 2 revisions, final delivered")
async def judge_revise(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Revize edilecek bir sayfa özeti ver")
    final = ctx.expect_final(run)
    verdicts = [s["verdict"] for s in run.traces if s["agent"] == "judge"]
    ctx.check(verdicts == ["revise", "pass"], f"judge verdicts: {verdicts}")
    if final:
        ctx.check("Düzeltilmiş" in final["answer"], f"revised result not used: {final['answer'][:80]}")


@scenario("judge_ask_user", doc="judge verdict=ask_user => need_confirm(judge.ask_user); approve continues")
async def judge_ask_user(ctx: Ctx) -> None:
    ext = await ctx.ext(confirm="approve")
    run = await ext.ask("Belirsiz bir sayfa işi")
    ctx.expect_final(run)
    ctx.check(len(run.confirms) == 1 and run.confirms[0]["tool"] == "judge.ask_user", f"confirms: {[c['tool'] for c in run.confirms]}")


# ==========================================================================
# 11. LLM failure modes
# ==========================================================================
@scenario("llm_invalid_json_router", doc="router returns garbage twice => safe chat fallback, request still answered")
async def llm_invalid_json_router(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("bozukjson merhaba", tab=None, timeout=30)
    ctx.expect_final(run)
    routers = [s for s in run.traces if s["agent"] == "router"]
    ctx.check(routers and routers[0]["decision"] == "Route: chat", "router fallback trace missing")


@scenario("llm_http_500", doc="LLM 500 on every call => retries then fallback answer (no hang, no crash)")
async def llm_http_500(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("llmhata merhaba", tab=None, timeout=60)
    ctx.check(not run.timed_out, "request hung on LLM 500s")
    ctx.check(run.final is not None or run.errors, "neither final nor error")
    ctx.check(ctx.llm.http_500_count >= 2, f"LLM retried only {ctx.llm.http_500_count} time(s)")


# ==========================================================================
# 12. protocol robustness
# ==========================================================================
@scenario("protocol_robustness", doc="busy-second-request, empty text, unknown type, invalid JSON, list frame")
async def protocol_robustness(ctx: Ctx) -> None:
    ext = await ctx.ext(tools="silent")
    rid, idx = await ext.start("Yavaş bir sayfa özeti")
    await ext.wait_for(lambda e: e.get("type") == "tool_call", timeout=20, since=idx)
    await ext.send({"type": "user_msg", "id": "r_second", "text": "Merhaba", "source": "text", "tab": None})
    busy = await ext.wait_for(lambda e: e.get("type") == "error" and e.get("request_id") == "r_second", timeout=5, since=idx)
    ctx.check(busy is not None, "second user_msg while busy did not yield an error frame")
    await ext.send({"type": "cancel", "id": rid})
    await ext.wait_for(lambda e: e.get("type") == "error" and e.get("request_id") == rid, timeout=8, since=idx)
    ext.tools_policy = "answer"

    n = len(ext.events)
    await ext.send({"type": "user_msg", "id": "r_empty", "text": "   ", "source": "text", "tab": None})
    e1 = await ext.wait_for(lambda e: e.get("type") == "error" and e.get("request_id") == "r_empty", timeout=5, since=n)
    ctx.check(e1 is not None, "empty user_msg did not yield an error frame")

    n = len(ext.events)
    await ext.send({"type": "bogus_type"})
    e2 = await ext.wait_for(lambda e: e.get("type") == "error", timeout=5, since=n)
    ctx.check(e2 is not None, "unknown frame type did not yield an error frame")

    n = len(ext.events)
    await ext.send_raw("this is { not json")
    e3 = await ext.wait_for(lambda e: e.get("type") == "error", timeout=5, since=n)
    ctx.check(e3 is not None and ext.connected, "invalid JSON text frame: expected an error frame and a live socket (INTERFACES §10: 'hata -> error frame, asla çökme')")

    if ext.connected:
        n = len(ext.events)
        await ext.send_raw("[1, 2, 3]")
        e4 = await ext.wait_for(lambda e: e.get("type") == "error", timeout=5, since=n)
        ctx.check(e4 is not None, "non-object JSON frame did not yield an error frame")

    # whatever happened to that socket, a fresh one must work
    ext2 = await ctx.ext()
    run = await ext2.ask("Merhaba", tab=None, timeout=20)
    ctx.expect_final(run, "fresh connection")


@scenario("busy_connection_isolation", doc="two sockets are independent (one request each, concurrently)")
async def busy_isolation(ctx: Ctx) -> None:
    a = await ctx.ext()
    b = await ctx.ext()
    ra, rb = await asyncio.gather(a.ask("Bu sayfayı özetle"), b.ask("Merhaba", tab=None))
    ctx.expect_final(ra, "A")
    ctx.expect_final(rb, "B")
    ctx.check(not any(e["type"] == "final" and e["request_id"] != rb.request_id for e in rb.events if e["type"] == "final"), "B received A's final")


# ==========================================================================
# 13. desktop
# ==========================================================================
@scenario("desktop_disabled_downgrade", doc="CUA off: desktop request never reaches a desktop.* tool")
async def desktop_disabled(ctx: Ctx) -> None:
    ext = await ctx.ext()
    run = await ext.ask("Masaüstünde metin editörünü aç ve 'Merhaba' yaz", tab=None)
    ctx.expect_final(run)
    route = ctx.route_of(run)
    ctx.check(route in ("chat", "browser_task"), f"desktop_task must be downgraded when CUA is off, got {route}")
    ctx.check(not any((s["tool"] or {}).get("name", "").startswith("desktop.") for s in run.traces), "desktop tool used while CUA is disabled")


@scenario("desktop_flow_fake_session", backend="cua", doc="CUA on + DESKTOP_FAKE=1: snapshot(shot evidence) -> click -> type -> snapshot; /shots serves the PNG")
async def desktop_flow(ctx: Ctx) -> None:
    async with httpx.AsyncClient() as client:
        h = (await client.get(f"{ctx.backend.http}/health")).json()
        ctx.check(h.get("cua_enabled") is True, f"/health cua_enabled={h.get('cua_enabled')}")
    ext = await ctx.ext()
    run = await ext.ask("Masaüstünde metin editörünü aç ve 'Merhaba' yaz", tab=None, timeout=90)
    final = ctx.expect_final(run)
    ctx.check(ctx.route_of(run) == "desktop_task", f"route should be desktop_task, got {ctx.route_of(run)}")
    ctx.check(not run.tool_calls, f"desktop.* must run in the backend, extension got {[c['name'] for c in run.tool_calls]}")
    shots = [e for s in run.traces for e in s["evidence"] if e.startswith("shot:")]
    ctx.check(shots, "no trace has `shot:` evidence from desktop.snapshot")
    exec_tools = [s["tool"]["name"] for s in run.traces if s["agent"] == "executor" and s["tool"]]
    for needed in ("desktop.snapshot", "desktop.click", "desktop.type"):
        ctx.check(needed in exec_tools, f"executor trace lacks {needed} ({exec_tools})")
    execs = ctx.llm.calls_for("executor")
    ctx.check(
        execs and "metnidir.Merhaba" in execs[-1].user,
        "fake desktop state did not change: second desktop.snapshot does not show the typed 'Merhaba' (click focus/type failed?)",
    )
    in_prompt = re.search(r"ekran görüntüsü: (/shots/\S+\.png)", " ".join(c.user for c in execs))
    path = shots[0][len("shot:"):] if shots else (in_prompt.group(1) if in_prompt else None)
    ctx.check(path, "no /shots path anywhere (trace evidence or snapshot rendering)")
    if path:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{ctx.backend.http}{path}")
            ctx.check(r.status_code == 200 and r.headers.get("content-type", "").startswith("image/png"), f"GET {path} -> {r.status_code} {r.headers.get('content-type')}")
    if ctx.failures:
        ctx.dump(run)
        ctx.note("backend log tail:\n    " + ctx.backend.log_tail(25).replace("\n", "\n    "))


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
async def run_scenario(stack: Stack, name: str) -> Result:
    backend_name, _doc, fn = SCENARIOS[name]
    started = time.monotonic()
    try:
        ctx = Ctx(stack, backend_name)
    except Exception as exc:
        return Result(name, False, time.monotonic() - started, [f"setup failed: {exc}"])
    try:
        await asyncio.wait_for(fn(ctx), timeout=150)
    except asyncio.TimeoutError:
        ctx.failures.append("scenario exceeded 150 s")
    except Exception as exc:  # harness-level exception => report, don't crash the run
        ctx.failures.append(f"exception: {exc!r}\n" + "".join(traceback.format_exc().splitlines(True)[-6:]))
    finally:
        try:
            await ctx.finish()
        except Exception as exc:
            ctx.failures.append(f"cleanup failed: {exc!r}")
    return Result(name, not ctx.failures, time.monotonic() - started, ctx.failures, ctx.notes)
