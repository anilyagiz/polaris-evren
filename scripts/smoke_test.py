"""LLM'siz, process-içi kabul testi (smoke test) — K3.

Çalıştırma (cwd = assistant/):

    python scripts/smoke_test.py

Gerçek ağ, gerçek LLM anahtarı, gerçek tarayıcı ve gerçek veritabanı
KULLANILMAZ: sahte LLM/bridge/send enjekte edilir, SQLite geçici dizinde
tutulur.  Her kontrol için PASS/FAIL satırı basılır; FAIL varsa exit code 1.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Console + environment must be ready before any backend import ----------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover - exotic console
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="smoke_test_"))
os.environ["DB_PATH"] = str(TMP_DIR / "smoke.db")
# Check 7 relies on a 1-second tool budget; set before config.get_settings().
os.environ["TOOL_TIMEOUT_S"] = "1.0"

# Compat: backend/llm.py does `from openai import APIError, AsyncOpenAI,
# AsyncTimeout`.  openai>=2 dropped `AsyncTimeout`, so that frozen module cannot
# be imported in this environment and every LLM-dependent module (orchestrator,
# agents) dies with it.  The test adds the alias *only inside this process* so
# the remaining acceptance criteria can still be measured; the incompatibility
# itself is reported as pre-check 0 and fails the run.
DEPENDENCY_NOTE = ""
try:
    import openai as _openai
except ImportError:  # pragma: no cover - openai is a declared dependency
    _openai = None
if _openai is not None and not hasattr(_openai, "AsyncTimeout"):
    _openai.AsyncTimeout = _openai.APITimeoutError  # type: ignore[attr-defined]
    DEPENDENCY_NOTE = (
        f"kurulu openai {_openai.__version__} 'AsyncTimeout' sunmuyor; "
        "backend/llm.py:17 importu bu ortamda kirik (openai>=2). "
        "Testin geri kalani icin surecte 'openai.AsyncTimeout = APITimeoutError' takma adi eklendi."
    )

from backend.bridge import BrowserBridge, new_call_id  # noqa: E402
from backend.config import get_settings, now_iso  # noqa: E402
from backend.llm import LLMError, ToolCall, ToolMessage, Usage  # noqa: E402
from backend.trace import TraceStore, make_step  # noqa: E402

PROMPTS_DIR = ROOT / "backend" / "prompts"


def _load_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        try:
            prompts[path.stem] = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable prompt
            continue
    return prompts


PROMPTS = _load_prompts()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def detect_agent(system: str) -> str:
    """Which agent does this system prompt belong to?  (prompt-file based)."""

    for stem, text in PROMPTS.items():
        if text and system.startswith(text):
            return stem
    head = system[:300].lower()
    for stem in PROMPTS:
        if f"sen {stem} ajanısın" in head:
            return stem
    for stem in PROMPTS:
        if stem in head:
            return stem
    return "unknown"


SPOKEN_TEST = (
    "Merhaba! Size nasıl yardımcı olabilirim? "
    "Ayrıca https://example.com/yardim adresine bakabilirsiniz."
)
ANSWER_TEST = (
    "**Merhaba!**\n\nNasıl yardımcı olabilirim? "
    "Ayrıntı: [yardım](https://example.com/yardim)"
)

SNAPSHOT: dict[str, Any] = {
    "source": "browser",
    "url": "https://shop.example.com/cart",
    "title": "Sepetim",
    "elements": [
        {"ref": 7, "role": "textbox", "name": "Ad Soyad", "value": ""},
        {"ref": 12, "role": "button", "name": "Satın Al", "value": None},
    ],
    "text_excerpt": "Sepetinizde 1 ürün var.",
}


class FakeLLM:
    """LLMClient double: deterministic answers, programmable tool scenario."""

    def __init__(self, route: str = "chat", judge_verdict: str = "pass") -> None:
        self.model = "fake-deepseek-chat"
        self.configured = True
        self.fail_json = False
        self.fail_tools = False
        self.route = route
        self.judge_verdict = judge_verdict

        self.json_agents: list[str] = []
        self.tool_agents: list[str] = []
        self.json_inputs: list[str] = []
        self.tool_inputs: list[str] = []
        self.tool_schemas: list[list[str]] = []
        self.tool_script: list[ToolMessage] = []
        self._script_index = 0

    # -- LLMClient surface ------------------------------------------------
    async def close(self) -> None:
        return None

    def _usage(self) -> Usage:
        return Usage(
            model=self.model,
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
            latency_ms=1,
            attempts=1,
        )

    def script(self, *messages: ToolMessage) -> None:
        self.tool_script = list(messages)
        self._script_index = 0

    def json_calls_for(self, agent: str) -> int:
        return sum(1 for name in self.json_agents if name == agent)

    def tool_calls_for(self, agent: str) -> int:
        return sum(1 for name in self.tool_agents if name == agent)

    def all_inputs(self) -> str:
        return "\n".join([*self.json_inputs, *self.tool_inputs])

    async def chat_json(
        self,
        system: str,
        messages: Any,
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], Usage]:
        agent = detect_agent(system)
        self.json_agents.append(agent)
        self.json_inputs.append(_dump(messages))
        if self.fail_json:
            raise LLMError("Sahte LLM hatası: bağlantı kurulamadı.")
        return self._json_payload(agent), self._usage()

    async def chat_tools(
        self,
        system: str,
        messages: Any,
        tools: Any,
        temperature: float = 0.2,
    ) -> tuple[ToolMessage, Usage]:
        agent = detect_agent(system)
        self.tool_agents.append(agent)
        self.tool_inputs.append(_dump(messages))
        names = [str(t.get("function", {}).get("name", "")) for t in tools or []]
        self.tool_schemas.append(names)
        if self.fail_tools:
            raise LLMError("Sahte LLM araç hatası: bağlantı kurulamadı.")
        if not self.tool_script:
            return ToolMessage(content="İş tamamlandı.", tool_calls=[]), self._usage()
        index = min(self._script_index, len(self.tool_script) - 1)
        self._script_index += 1
        template = self.tool_script[index]
        return (
            ToolMessage(content=template.content, tool_calls=list(template.tool_calls)),
            self._usage(),
        )

    def _json_payload(self, agent: str) -> dict[str, Any]:
        if agent == "router":
            return {
                "route": self.route,
                "confidence": 0.9,
                "why": "Kullanıcı isteği incelendi ve rota seçildi.",
            }
        if agent == "planner":
            return {
                "goal": "Kullanıcı isteğini tamamla",
                "steps": [
                    {"id": 1, "action": "Sayfayı incele", "why": "Durumu görmek gerekiyor."},
                    {"id": 2, "action": "Sonucu özetle", "why": "Kullanıcıya cevap verilecek."},
                ],
            }
        if agent == "judge":
            return {
                "scores": {"correct": 4, "complete": 3, "safe": 5},
                "verdict": self.judge_verdict,
                "feedback": "Eksik adım var, plan gözden geçirilmeli.",
            }
        if agent == "responder":
            return {
                "answer": ANSWER_TEST,
                "spoken": SPOKEN_TEST,
                "citations": [{"title": "Yardım", "url": "https://example.com/yardim", "ref": 1}],
            }
        return {}


def tool_call(name: str, args: dict[str, Any], call_id: str = "t1") -> ToolMessage:
    return ToolMessage(content="", tool_calls=[ToolCall(id=call_id, name=name, args=args)])


class FakeBridge:
    """BrowserBridge double: pinned snapshot, programmable confirm answer."""

    def __init__(
        self,
        *,
        confirm_answer: bool = True,
        hang: bool = False,
        snapshot: dict[str, Any] | None = None,
        send: Any | None = None,
    ) -> None:
        self.request_id: str | None = None
        self.confirm_answer = confirm_answer
        self.hang = hang
        self.snapshot = dict(snapshot or SNAPSHOT)
        self.events: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.confirms: list[dict[str, Any]] = []
        self.closed = False
        self._send = send

    # -- BrowserBridge surface --------------------------------------------
    def attach(self, send: Any) -> None:
        """Bind the socket writer so protocol frames look like the real bridge."""

        self._send = send

    def set_request_id(self, request_id: str | None) -> None:
        self.request_id = request_id

    async def call(
        self, name: str, args: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append({"name": name, "args": dict(args or {})})
        self.events.append({"kind": "call", "name": name})
        if self.hang:
            await asyncio.sleep(60)
        if name == "browser.snapshot":
            return {"ok": True, "data": dict(self.snapshot)}
        return {"ok": True, "data": {"ok": True}}

    async def request_confirm(
        self,
        call_id: str,
        summary: str,
        tool: str,
        args: dict[str, Any],
        timeout: float | None = None,
    ) -> bool:
        self.confirms.append(
            {"call_id": call_id, "summary": summary, "tool": tool, "args": dict(args or {})}
        )
        self.events.append({"kind": "confirm", "name": tool})
        # The real BrowserBridge emits the need_confirm frame; mirror that here so
        # the protocol itself (not just the return value) is under test.
        if self._send is not None:
            await self._send(
                {
                    "type": "need_confirm",
                    "call_id": call_id,
                    "request_id": self.request_id,
                    "summary": summary,
                    "tool": tool,
                    "args": dict(args or {}),
                }
            )
        return bool(self.confirm_answer)

    def resolve_tool_result(
        self,
        call_id: str,
        ok: bool,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        return False

    def resolve_confirm(self, call_id: str, approved: bool) -> bool:
        return False

    async def close(self) -> None:
        self.closed = True

    @property
    def pending_count(self) -> int:
        return 0

    # -- helpers -----------------------------------------------------------
    def names(self) -> list[str]:
        return [str(item["name"]) for item in self.calls]

    def first_event_index(self, kind: str, name: str) -> int:
        for index, event in enumerate(self.events):
            if event["kind"] == kind and event["name"] == name:
                return index
        return -1


class SlowBridge(FakeBridge):
    """Bridge whose tool calls answer late: used to measure the request budget."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def call(
        self, name: str, args: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append({"name": name, "args": dict(args or {})})
        self.events.append({"kind": "call", "name": name})
        await asyncio.sleep(self.delay)
        if name == "browser.snapshot":
            return {"ok": True, "data": dict(self.snapshot)}
        return {"ok": True, "data": {"ok": True}}


class RecordedSend:
    """`send` double: keeps every server->client frame in order."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, frame: dict[str, Any]) -> None:
        self.frames.append(dict(frame))

    def of_type(self, frame_type: str) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("type") == frame_type]

    def text(self) -> str:
        return "\n".join(_dump(frame) for frame in self.frames)

    def types(self) -> list[str]:
        return [str(f.get("type")) for f in self.frames]


@dataclass
class Session:
    """One orchestrator + its doubles."""

    orch: Any
    send: RecordedSend
    bridge: Any
    llm: FakeLLM
    store: TraceStore
    frames: list[dict[str, Any]] = field(default_factory=list)

    async def pump(self, frame: dict[str, Any], timeout: float = 20.0) -> bool:
        await self.orch.handle_frame(frame)
        try:
            await asyncio.wait_for(self.orch.wait_idle(timeout=timeout), timeout=timeout + 5.0)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        try:
            await self.orch.close()
        except Exception:  # pragma: no cover - defensive
            pass

    def text(self) -> str:
        return self.send.text()


async def new_session(
    *,
    llm: FakeLLM | None = None,
    bridge: Any | None = None,
    store: TraceStore | None = None,
) -> Session:
    from backend.orchestrator import Orchestrator

    send = RecordedSend()
    model = llm or FakeLLM()
    browser = bridge if bridge is not None else FakeBridge()
    if hasattr(browser, "attach"):
        browser.attach(send)
    trace_store = store or TraceStore(str(TMP_DIR / "traces.db"))
    orch = Orchestrator(send, store=trace_store, bridge=browser, client=model)
    await orch.start()
    await orch.handle_frame({"type": "hello", "client": "smoke_test"})
    return Session(orch=orch, send=send, bridge=browser, llm=model, store=trace_store)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    line = f"{'PASS' if ok else 'FAIL'} - {name}"
    if detail:
        line += f"  | {detail}"
    print(line, flush=True)


def sentence_count(text: str) -> int:
    return len([p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()])


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_dependencies() -> tuple[bool, str]:
    """Pre-check 0: can the frozen backend modules be imported as shipped?"""

    if DEPENDENCY_NOTE:
        return False, DEPENDENCY_NOTE
    return True, "backend.llm / backend.orchestrator bağımlılıkları temiz import edildi"


def check_health() -> tuple[bool, str]:
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        response = client.get("/health")
    if response.status_code != 200:
        return False, f"GET /health -> HTTP {response.status_code}"
    payload = response.json()
    if payload.get("ok") is not True:
        return False, f"ok alanı True değil: {payload!r}"
    return True, f"HTTP 200, ok=True (llm_configured={payload.get('llm_configured')})"


async def check_chat_flow() -> tuple[bool, str]:
    llm = FakeLLM(route="chat")
    session = await new_session(llm=llm)
    request_id = "r_chat_1"
    try:
        finished = await session.pump(
            {"type": "user_msg", "id": request_id, "text": "merhaba", "source": "text", "tab": None}
        )
        finals = [f for f in session.send.of_type("final") if f.get("request_id") == request_id]
        if not finals:
            return False, (
                f"final frame yok (bitti={finished}, frame'ler={session.send.types()})"
            )
        final = finals[0]
        spoken = str(final.get("spoken") or "")
        if not spoken.strip():
            return False, "final.spoken boş"
        if "http" in spoken.lower() or "www." in spoken.lower():
            return False, f"final.spoken URL içeriyor: {spoken!r}"
        count = sentence_count(spoken)
        if count > 2:
            return False, f"final.spoken {count} cümle (>2): {spoken!r}"
        return True, f"final var, spoken={count} cümle, URL yok: {spoken!r}"
    finally:
        await session.close()


async def check_trace_persistence() -> tuple[bool, str]:
    store = TraceStore(str(TMP_DIR / "trace_persist.db"))
    await store.init()
    request_id = "r_trace_1"
    await store.save(request_id, make_step("router", "Route: chat", "Selamlama isteği", confidence=0.9))
    await store.save(
        request_id,
        make_step(
            "executor",
            "Araç çağrısı: browser.snapshot",
            "Sayfayı görmem gerekiyor",
            tool={"name": "browser.snapshot", "args": {"why": "durum"}},
        ),
    )
    await store.save(
        request_id,
        make_step(
            "judge",
            "Değerlendirme: pass",
            "Sonuç yeterli",
            scores={"correct": 5, "complete": 4, "safe": 5},
            verdict="pass",
        ),
    )
    rows = await store.history(request_id)
    if len(rows) != 3:
        return False, f"history 3 satır bekleniyordu, {len(rows)} geldi"
    for index, row in enumerate(rows):
        for field_name in ("agent", "decision", "why", "ts"):
            if not str(row.get(field_name) or "").strip():
                return False, f"satır {index} alanı boş: {field_name} ({row!r})"
    if rows[1].get("tool_name") != "browser.snapshot":
        return False, f"tool_name kalıcı olmamış: {rows[1].get('tool_name')!r}"
    if not rows[1].get("tool_args"):
        return False, "tool_args kalıcı olmamış"
    if "correct" not in str(rows[2].get("scores") or ""):
        return False, f"scores kalıcı olmamış: {rows[2].get('scores')!r}"
    if rows[2].get("verdict") != "pass":
        return False, f"verdict kalıcı olmamış: {rows[2].get('verdict')!r}"
    other = await store.history("r_yok")
    if other:
        return False, f"başka istek için {len(other)} satır döndü"
    return True, f"3 adım sqlite'tan geri okundu; agent/decision/why/ts dolu (ts={rows[0]['ts']})"


async def _confirm_scenario(approved: bool) -> tuple[Session, str]:
    llm = FakeLLM(route="browser_task")
    llm.script(
        tool_call("browser__snapshot", {"why": "Tıklama hedefini görmek istiyorum"}, "t1"),
        tool_call("browser__click", {"ref": 12, "why": "Satın alma düğmesine basacağım"}, "t2"),
        tool_call("finish", {"why": "İş bitti", "summary": "Sepet işlemi tamamlandı"}, "t3"),
    )
    bridge = FakeBridge(confirm_answer=approved)
    session = await new_session(llm=llm, bridge=bridge)
    return session, "r_confirm_" + ("yes" if approved else "no")


async def check_confirm_approve() -> tuple[bool, str]:
    session, request_id = await _confirm_scenario(True)
    try:
        await session.pump(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "sepetteki ürünü satın al",
                "source": "text",
                "tab": {"id": 1, "title": "Sepetim", "url": "https://shop.example.com/cart"},
            }
        )
        confirms = [f for f in session.send.of_type("need_confirm") if f.get("request_id") == request_id]
        if not confirms:
            return False, f"need_confirm frame yok (frame'ler={session.send.types()})"
        if "browser.click" not in session.bridge.names():
            return False, f"onay verilmesine rağmen click handler çağrılmadı: {session.bridge.names()}"
        confirm_at = session.bridge.first_event_index("confirm", "browser.click")
        click_at = session.bridge.first_event_index("call", "browser.click")
        if confirm_at == -1 or click_at == -1 or click_at < confirm_at:
            return False, f"tıklama onaydan önce/sonra yanlış sırada (confirm@{confirm_at}, call@{click_at})"
        return True, (
            f"need_confirm üretildi ({confirms[0].get('call_id')}), onay True -> "
            f"browser.click köprüye gitti; summary={confirms[0].get('summary')!r}"
        )
    finally:
        await session.close()


async def check_confirm_reject() -> tuple[bool, str]:
    session, request_id = await _confirm_scenario(False)
    try:
        await session.pump(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "sepetteki ürünü satın al",
                "source": "text",
                "tab": {"id": 1, "title": "Sepetim", "url": "https://shop.example.com/cart"},
            }
        )
        confirms = [f for f in session.send.of_type("need_confirm") if f.get("request_id") == request_id]
        if not confirms:
            return False, f"need_confirm frame yok (frame'ler={session.send.types()})"
        if "browser.click" in session.bridge.names():
            return False, f"reddedilmesine rağmen click handler çağrıldı: {session.bridge.names()}"

        store_text = _dump(await session.store.history(request_id))
        haystack = (session.text() + "\n" + session.llm.all_inputs() + "\n" + store_text).casefold()
        if "reddetti" not in haystack:
            return False, "'kullanıcı reddetti' bilgisi history/trace/frame'lerde yok"
        return True, (
            f"need_confirm üretildi, click çağrılmadı ({session.bridge.names()}), "
            "executor/geçmişte 'reddetti' bilgisi görünüyor"
        )
    finally:
        await session.close()


async def check_revise_loop() -> tuple[bool, str]:
    settings = get_settings()
    expected = 1 + int(settings.max_revisions)
    llm = FakeLLM(route="browser_task", judge_verdict="revise")
    llm.script(tool_call("finish", {"why": "İş bitti", "summary": "Sonuç hazır"}, "t1"))
    session = await new_session(llm=llm)
    request_id = "r_revise"
    started = time.monotonic()
    try:
        finished = await session.pump(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "sepetteki ürünü kontrol et",
                "source": "text",
                "tab": None,
            },
            timeout=20.0,
        )
        elapsed = time.monotonic() - started
        calls = llm.tool_calls_for("executor")
        if not finished:
            return False, "istek 20 sn içinde bitmedi (sonsuz döngü şüphesi)"
        if calls == 0:
            return False, "executor hiç çalışmadı"
        if calls > expected:
            return False, (
                f"executor {calls} kez çağrıldı; en fazla {expected} bekleniyordu "
                f"(1 + max_revisions={settings.max_revisions})"
            )
        if not (session.send.of_type("final") or session.send.of_type("error")):
            return False, f"istek final/error frame'i olmadan bitti (frame'ler={session.send.types()})"
        return True, (
            f"executor {calls} tur (üst sınır {expected}), judge {llm.json_calls_for('judge')} kez "
            f"değerlendirdi, {elapsed:.2f} sn'de bitti — sonsuz döngü yok"
        )
    finally:
        await session.close()


async def check_tool_timeout() -> tuple[bool, str]:
    # (a) bridge level: an extension that never answers must not deadlock.
    async def silent_send(frame: dict[str, Any]) -> None:
        return None

    bridge = BrowserBridge(silent_send, request_id="r_timeout_direct")
    started = time.monotonic()
    result = await bridge.call("browser.snapshot", {}, timeout=1.0)
    direct_elapsed = time.monotonic() - started
    if result.get("ok") is not False:
        return False, f"bridge.call zaman aşımında {{'ok': False}} dönmedi: {result!r}"
    if direct_elapsed >= 5.0:
        return False, f"bridge.call {direct_elapsed:.2f} sn sürdü (>5 sn)"

    # (b) request level: same behaviour through the orchestrator + real bridge.
    llm = FakeLLM(route="browser_task")
    llm.script(
        tool_call("browser__snapshot", {"why": "Sayfa durumunu görmek istiyorum"}, "t1"),
        tool_call("finish", {"why": "Bilgi yok, bitiriyorum", "summary": "Sayfa okunamadı"}, "t2"),
    )
    session = await new_session(
        llm=llm, bridge=BrowserBridge(silent_send, request_id="r_timeout_req")
    )
    request_id = "r_timeout_req"
    started = time.monotonic()
    try:
        finished = await session.pump(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "sepeti oku",
                "source": "text",
                "tab": None,
            },
            timeout=20.0,
        )
        request_elapsed = time.monotonic() - started
        haystack = (session.text() + "\n" + llm.all_inputs()).casefold()
        if not finished:
            return False, f"istek takıldı (>{20.0:.0f} sn); zaman aşımı kilitlemeye yol açtı"
        if request_elapsed >= 5.0:
            return False, f"istek {request_elapsed:.2f} sn sürdü (>5 sn, tool_timeout=1 sn)"
        if "zaman" not in haystack:
            return False, "araç zaman aşımı bilgisi trace/geçmişte görünmüyor"
        return True, (
            f"bridge.call 1 sn'de {{'ok': False}} ({direct_elapsed:.2f} sn); "
            f"istek {request_elapsed:.2f} sn'de bitti, zaman aşımı raporlandı"
        )
    finally:
        await session.close()


async def check_cancel() -> tuple[bool, str]:
    llm = FakeLLM(route="browser_task")
    llm.script(tool_call("browser__snapshot", {"why": "Sayfayı okumam gerekiyor"}, "t1"))
    session = await new_session(llm=llm, bridge=FakeBridge(hang=True))
    request_id = "r_cancel"
    try:
        await session.orch.handle_frame(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "uzun süren sayfa işlemi",
                "source": "text",
                "tab": None,
            }
        )
        # Wait until the fake tool is actually in flight, then cancel.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.bridge.calls:
                break
            await asyncio.sleep(0.02)
        if not session.bridge.calls:
            return False, (
                "araç çağrısı başlamadan iptal testi durdu "
                f"(bridge çağrıları={session.bridge.names()}, frame'ler={session.send.types()})"
            )

        started = time.monotonic()
        await session.orch.handle_frame({"type": "cancel", "request_id": request_id})
        try:
            await asyncio.wait_for(session.orch.wait_idle(timeout=5.0), timeout=6.0)
        except asyncio.TimeoutError:
            return False, "cancel sonrası wait_idle dönmedi (>5 sn); görev iptal edilmedi"
        idle_elapsed = time.monotonic() - started

        errors = [f for f in session.send.of_type("error") if f.get("request_id") == request_id]
        if not errors:
            return False, f"cancel sonrası error frame yok (frame'ler={session.send.types()})"
        if idle_elapsed >= 5.0:
            return False, f"wait_idle {idle_elapsed:.2f} sn sürdü (>=5 sn)"
        return True, (
            f"error frame gönderildi ({errors[0].get('message')!r}), "
            f"wait_idle {idle_elapsed:.2f} sn'de döndü"
        )
    finally:
        await session.close()


async def check_tool_call_limit() -> tuple[bool, str]:
    settings = get_settings()
    llm = FakeLLM(route="browser_task")
    llm.script(tool_call("browser__snapshot", {"why": "Sayfa durumunu yeniden okuyacağım"}, "t1"))
    session = await new_session(llm=llm)
    request_id = "r_limit"
    try:
        finished = await session.pump(
            {
                "type": "user_msg",
                "id": request_id,
                "text": "sayfayı sürekli incele",
                "source": "text",
                "tab": None,
            },
            timeout=25.0,
        )
        calls = session.bridge.calls
        if not finished:
            return False, "istek 25 sn içinde bitmedi; sınır uygulanmıyor olabilir"
        if not calls:
            return False, "hiç araç çağrısı gözlenmedi"
        if len(calls) > int(settings.max_tool_calls):
            breakdown: dict[str, int] = {}
            for call in calls:
                breakdown[str(call["name"])] = breakdown.get(str(call["name"]), 0) + 1
            return False, (
                f"toplam {len(calls)} araç çağrısı > max_tool_calls={settings.max_tool_calls} "
                f"({breakdown})"
            )
        return True, (
            f"sürekli araç çağıran executor {len(calls)} çağrıda durdu "
            f"(max_tool_calls={settings.max_tool_calls})"
        )
    finally:
        await session.close()


async def check_llm_error() -> tuple[bool, str]:
    llm = FakeLLM(route="chat")
    llm.configured = False
    llm.fail_json = True
    llm.fail_tools = True
    session = await new_session(llm=llm)
    request_id = "r_llmerr"
    try:
        finished = await session.pump(
            {"type": "user_msg", "id": request_id, "text": "merhaba", "source": "text", "tab": None},
            timeout=15.0,
        )
        if not finished:
            return False, "istek 15 sn içinde bitmedi"
        errors = [f for f in session.send.of_type("error") if f.get("request_id") == request_id]

        # The connection must survive either way: a fresh, healthy request must work.
        llm.configured = True
        llm.fail_json = False
        llm.fail_tools = False
        llm.route = "chat"
        second_id = "r_llmerr_2"
        ok_second = await session.pump(
            {"type": "user_msg", "id": second_id, "text": "tekrar merhaba", "source": "text", "tab": None},
            timeout=15.0,
        )
        finals_second = [f for f in session.send.of_type("final") if f.get("request_id") == second_id]
        alive = bool(ok_second and finals_second)
        alive_text = (
            "ardından gelen istek yine final üretti, sunucu ayakta"
            if alive
            else f"ardından gelen istek de çalışmadı (frame'ler={session.send.types()})"
        )

        if not errors:
            finals = session.send.of_type("final")
            fallback = finals[0].get("answer") if finals else None
            return False, (
                "LLMError fırlatılmasına rağmen 'error' frame'i gönderilmedi; "
                f"frame'ler={session.send.types()}, final.answer={fallback!r} "
                "(router/responder/planner LLMError'ı yutup yedek cevaba düşüyor); "
                + alive_text
            )
        if not alive:
            return False, f"error frame gönderildi ({errors[0].get('message')!r}) ama {alive_text}"
        return True, (
            f"error frame gönderildi ({errors[0].get('message')!r}); {alive_text}"
        )
    finally:
        await session.close()


async def check_tool_envelope() -> tuple[bool, str]:
    """11. §7 handler contract: browser.* handlers return {ok, data, error}.

    `browser.read`/`browser.snapshot` used to return bare payloads, which the
    orchestrator rejected as invalid tool results (every page read became an
    error); `browser.type` must also hand the extension a real boolean `submit`.
    """

    from backend.tools import browser as browser_tools
    from backend.tools.registry import ToolContext

    class _Bridge:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def call(self, name: str, args: dict[str, Any], timeout: float | None = None):
            self.sent.append((name, dict(args or {})))
            return dict(self.payload)

    cases = (
        (
            "browser.snapshot",
            browser_tools._snapshot,
            {"ok": True, "data": {"source": "browser", "elements": [{"ref": 1, "role": "button", "name": "Ara"}]}},
        ),
        ("browser.read", browser_tools._read, {"ok": True, "data": {"text": "Sayfa metni", "url": "https://x"}}),
        ("browser.navigate", browser_tools._navigate, {"ok": True, "data": {"url": "https://x", "title": "T"}}),
    )
    checked: list[str] = []
    for label, handler, payload in cases:
        bridge = _Bridge(payload)
        ctx = ToolContext(request_id="r_env", bridge=bridge, tab=None, emit_trace=None, extras={})
        out = await handler({}, ctx)
        if not isinstance(out, dict) or out.get("ok") is not True or "data" not in out:
            return False, f"{label} §7 zarfı döndürmedi: {out!r}"
        checked.append(label)

    bridge = _Bridge({"ok": True, "data": {}})
    ctx = ToolContext(request_id="r_env", bridge=bridge, tab=None, emit_trace=None, extras={})
    await browser_tools._type({"ref": 3, "text": "x", "submit": "true"}, ctx)
    forwarded = bridge.sent[-1][1]
    if forwarded.get("submit") is not True:
        return False, f"browser.type submit normalleştirilmedi (köprüye giden: {forwarded!r})"
    return True, (
        f"{', '.join(checked)} -> {{'ok': True, 'data': ...}}; "
        "browser.type submit='true' -> köprüye submit=True"
    )


async def check_request_budget() -> tuple[bool, str]:
    """12. `request_budget_s` is a *total*: a slow tool cannot overshoot it."""

    previous = os.environ.get("REQUEST_BUDGET_S")
    os.environ["REQUEST_BUDGET_S"] = "0.5"
    get_settings.cache_clear()
    bridge = SlowBridge(2.0)
    try:
        llm = FakeLLM(route="browser_task")
        llm.script(tool_call("browser__snapshot", {"why": "Sayfayı yeniden okuyacağım"}, "t1"))
        session = await new_session(llm=llm, bridge=bridge)
        started = time.monotonic()
        try:
            finished = await session.pump(
                {
                    "type": "user_msg",
                    "id": "r_budget",
                    "text": "sayfayı sürekli incele",
                    "source": "text",
                    "tab": None,
                },
                timeout=20.0,
            )
        finally:
            elapsed = time.monotonic() - started
            await session.close()
    finally:
        if previous is None:
            os.environ.pop("REQUEST_BUDGET_S", None)
        else:
            os.environ["REQUEST_BUDGET_S"] = previous
        get_settings.cache_clear()

    if not finished:
        return False, "istek 20 sn içinde bitmedi"
    if not bridge.calls:
        return False, "araç hiç çağrılmadı (bütçe testi anlamsız)"
    if elapsed >= 1.5:
        return False, (
            f"bütçe 0.5 sn iken istek {elapsed:.2f} sn sürdü "
            f"(aşım {elapsed - 0.5:.2f} sn, araç çağrısı {len(bridge.calls)})"
        )
    return True, (
        f"0.5 sn bütçe + 2 sn yanıt gecikmesi -> istek {elapsed:.2f} sn'de bitti "
        f"(aşım {elapsed - 0.5:.2f} sn), araç çağrısı {len(bridge.calls)}"
    )


def check_mock_ws_e2e() -> tuple[bool, str]:
    """13. scripts/mock_ws.py: one user_msg -> one final, schema-valid tool_call.

    The fixture holds two scenarios, so replaying its `final` rows used to make a
    single request end with three finals; the mock must replay only trace /
    tool_call / need_confirm rows and own the single `final` itself.
    """

    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    os.environ.setdefault("MOCK_FIXTURE_DELAY_S", "0.0")

    from fastapi.testclient import TestClient

    import mock_ws
    from backend.tools import registry

    replay = [event for event in mock_ws.load_fixture_events() if event.get("type") != "final"]
    if not replay:
        return False, "fixture olayı yüklenemedi"

    with TestClient(mock_ws.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "client": "smoke_test"})
            ws.send_json(
                {"type": "user_msg", "id": "r_mock", "text": "sepete ekle", "source": "text"}
            )
            frames = [ws.receive_json() for _ in range(len(replay) + 1)]
            call = frames[-1]
            if call.get("type") != "tool_call":
                return False, f"son frame tool_call değil: {call.get('type')!r}"
            tool = registry.get(str(call.get("name")))
            if tool is None:
                return False, f"tool_call kayıtlı olmayan aracı çağırıyor: {call.get('name')!r}"
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            declared = set((tool.parameters.get("properties") or {}).keys())
            undeclared = sorted(set(args) - declared)
            if undeclared:
                return False, f"{tool.name} args şemada tanımsız: {undeclared}"
            ws.send_json(
                {
                    "type": "tool_result",
                    "call_id": call.get("call_id"),
                    "ok": True,
                    "data": {"elements": []},
                }
            )
            tail = ws.receive_json()

    finals = [frame for frame in frames if frame.get("type") == "final"]
    if tail.get("type") == "final":
        finals.append(tail)
    if len(finals) != 1:
        return False, f"tek istekte {len(finals)} final frame var (beklenen 1)"
    need_confirm = [frame for frame in frames if frame.get("type") == "need_confirm"]
    return True, (
        f"fixture'dan {len(replay)} olay oynatıldı, {len(need_confirm)} need_confirm, "
        f"tek final; browser.snapshot args={args} şemaya uygun"
    )


CHECKS: tuple[tuple[str, Any], ...] = (
    ("0. bağımlılık sağlığı: backend.llm importu", check_dependencies),
    ("1. /health 200 + ok=True", check_health),
    ("2. chat akışı: final + konuşma metni kuralları", check_chat_flow),
    ("3. trace kalıcılığı (sqlite -> history)", check_trace_persistence),
    ("4. onay kapısı ONAY -> click çalıştı", check_confirm_approve),
    ("5. onay kapısı RED -> click çalışmadı", check_confirm_reject),
    ("6. revise döngüsü sınırlı (max_revisions)", check_revise_loop),
    ("7. tool timeout kilitlemiyor", check_tool_timeout),
    ("8. cancel -> error frame + görev iptali", check_cancel),
    ("9. tool çağrısı limiti (max_tool_calls)", check_tool_call_limit),
    ("10. LLM hatası -> error frame, sunucu ayakta", check_llm_error),
    ("11. tool sözleşmesi: {ok,data,error} zarfı + submit normalleştirme", check_tool_envelope),
    ("12. request bütçesi toplam: yavaş araç bütçeyi aşamaz", check_request_budget),
    ("13. mock_ws uçtan uca: tek istek -> tek final + şemaya uygun tool_call", check_mock_ws_e2e),
)


async def main() -> int:
    print(f"Python {sys.version.split()[0]} | proje kökü: {ROOT}")
    print(f"geçici SQLite: {TMP_DIR}")
    print(f"tool_timeout_s={get_settings().tool_timeout_s}, "
          f"max_tool_calls={get_settings().max_tool_calls}, "
          f"max_revisions={get_settings().max_revisions}")
    if DEPENDENCY_NOTE:
        print("!" * 78)
        print(f"UYARI: {DEPENDENCY_NOTE}")
        print("!" * 78)
    try:
        from backend import orchestrator as orchestrator_module

        print(f"orchestrator: {orchestrator_module.__file__}")
    except Exception as exc:  # pragma: no cover - reported per check
        print(f"orchestrator yüklenemedi: {type(exc).__name__}: {exc}")
    for module_name in ("browser", "desktop", "search", "memory", "reminders"):
        try:
            __import__(f"backend.tools.{module_name}")
        except Exception as exc:
            print(f"opsiyonel tool modülü yok: backend.tools.{module_name} ({type(exc).__name__})")
    print("-" * 78)

    for name, func in CHECKS:
        try:
            outcome = func()
            if asyncio.iscoroutine(outcome):
                ok, detail = await outcome
            else:
                ok, detail = outcome
        except Exception as exc:
            frames = traceback.extract_tb(sys.exc_info()[2])
            frame = frames[-1] if frames else None
            where = f"{frame.filename}:{frame.lineno}" if frame else "?"
            ok, detail = False, f"beklenmeyen hata {type(exc).__name__}: {exc} [{where}]"
        record(name, ok, detail)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 78)
    print(f"ÖZET: {passed}/{len(RESULTS)} PASS, {failed} FAIL")
    if failed:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - FAIL: {name} -> {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(130) from None
