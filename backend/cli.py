"""Extension-free CLI harness for the assistant orchestrator.  Owner: K3.

Runs the real reasoning pipeline (router -> planner -> executor -> judge ->
responder) plus the deterministic confirmation gate in the terminal, so prompts
can be iterated without Brave, the extension or the WebSocket server:

    python -m backend.cli "merhaba" --offline
    python -m backend.cli "Bu sayfayi ozetle" --offline --tab-url https://example.com --trace

`--offline` swaps the DeepSeek client for :class:`FakeLLM` and the WebSocket
bridge for :class:`LocalBridge`, which answers every `browser.*` call instantly
with realistic Turkish mock data (including one risky "Siparisi tamamla" button
so the confirmation gate can be exercised).  At an interactive terminal the gate
asks `[e/H]`; `-y/--yes` answers it automatically, which is how the risky path is
tested without a human at the keyboard.  Without a terminal (piped/CI runs) the
gate is rejected at once instead of blocking for `confirm_timeout_s`.

Exit codes: 0 = final received, 1 = error frame / no answer, 2 = missing
configuration (API key or an importable orchestrator).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import secrets
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# openai 2.x compatibility
# ---------------------------------------------------------------------------
# `backend/llm.py` (frozen, not owned by this file) does
# `from openai import APIError, AsyncOpenAI, AsyncTimeout`, but openai 2.x no
# longer exports `AsyncTimeout`.  Without this in-process alias every backend
# import (llm -> agents -> orchestrator) fails against the installed SDK.
# It is a no-op as soon as llm.py is fixed upstream.
import openai as _openai  # noqa: E402

if not hasattr(_openai, "AsyncTimeout"):
    _openai.AsyncTimeout = _openai.APITimeoutError  # type: ignore[attr-defined]
    logger.info("openai %s: AsyncTimeout -> APITimeoutError takma adı eklendi", _openai.__version__)

from .bridge import BrowserBridge  # noqa: E402
from .config import ensure_data_dir, get_settings  # noqa: E402
from .llm import ToolCall, ToolMessage, Usage, get_llm  # noqa: E402
from .tools import registry  # noqa: E402
from .trace import TraceStore  # noqa: E402

#: Frames are the frozen WebSocket protocol dictionaries (section 6).
Frame = dict[str, Any]
SendFn = Callable[[Frame], Awaitable[None]]

USAGE_HINT = 'Kullanım: python -m backend.cli "soru" [--offline] [--trace] [--json] [--tab-url URL] [--tab-title METİN] [-y]'
NO_KEY_MESSAGE = "DEEPSEEK_API_KEY yok. backend/.env doldur ya da --offline kullan."
NO_ORCHESTRATOR_MESSAGE = "Orchestrator yüklenemedi: backend/orchestrator.py hazır değil."

DEFAULT_URL = "https://example.com"
DEFAULT_TITLE = "Örnek Alışveriş Sayfası"
#: Ref of the deliberately risky element inside the fake snapshot.
RISKY_REF = 9

_FAKE_PAGE_TEXT = (
    "Bu sayfa, çevrimdışı CLI testi için üretilmiş sahte bir alışveriş sayfasıdır. "
    "Sayfanın üst kısmında bir arama kutusu ve “Ara” düğmesi bulunur. "
    "Listede üç ürün vardır: Kablosuz Kulaklık 1.299 TL, Mekanik Klavye 899 TL ve "
    "USB-C Hub 549 TL. Kargo bilgisi: 500 TL ve üzeri siparişlerde kargo ücretsizdir. "
    "İade koşulları: teslimattan sonra 14 gün içinde koşulsuz iade hakkı vardır. "
    "Ödeme seçenekleri: kredi kartı, banka kartı ve kapıda ödeme. "
    "Kampanyalar bölümünde hafta sonuna özel yüzde on indirim duyurulmuştur. "
    "Kupon kodu alanı boş, “Sözleşmeyi okudum” kutusu işaretlenmemiş durumdadır."
)

_FAKE_ELEMENTS: tuple[Frame, ...] = (
    {"ref": 1, "role": "link", "name": "Giriş yap"},
    {"ref": 2, "role": "link", "name": "Sepetim"},
    {"ref": 3, "role": "textbox", "name": "Arama kutusu", "value": ""},
    {"ref": 4, "role": "button", "name": "Ara"},
    {"ref": 5, "role": "heading", "name": "Ürünler"},
    {"ref": 6, "role": "article", "name": "Kablosuz Kulaklık", "value": "1.299 TL"},
    {"ref": 7, "role": "article", "name": "Mekanik Klavye", "value": "899 TL"},
    {"ref": 8, "role": "article", "name": "USB-C Hub", "value": "549 TL"},
    {"ref": RISKY_REF, "role": "button", "name": "Siparişi tamamla"},
    {"ref": 10, "role": "link", "name": "Kampanyalar"},
    {"ref": 11, "role": "textbox", "name": "Kupon kodu", "value": ""},
    {"ref": 12, "role": "checkbox", "name": "Sözleşmeyi okudum", "value": "hayır"},
)

# ---------------------------------------------------------------------------
# Text helpers (Turkish-aware folding keeps the mock deterministic)
# ---------------------------------------------------------------------------
_FOLD_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "ğ": "g",
        "ü": "u",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)


def _fold(text: str) -> str:
    return text.translate(_FOLD_MAP).lower()


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _content_of(message: Frame) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _all_text(messages: Sequence[Frame]) -> str:
    return "\n".join(_content_of(message) for message in messages)


def _last_user_content(messages: Sequence[Frame]) -> str:
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            content = _content_of(message).strip()
            if content:
                return content
    return ""


def _field(text: str, label: str) -> str:
    """Read `Label: value` (optionally behind markdown hashes) from a prompt blob."""

    match = re.search(rf"^#*\s*{re.escape(label)}\s*:?\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _first_url(text: str) -> str:
    match = re.search(r"https?://[^\s\"'<>)\]]+", text)
    return match.group(0).rstrip(".,;") if match else ""


def _title_from_url(url: str) -> str:
    text = url.strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.split("/", 1)[0] or url


# ---------------------------------------------------------------------------
# FakeLLM: deterministic stand-in for LLMClient (--offline)
# ---------------------------------------------------------------------------
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("judge", ("judge", "denetci", "verdict", "scores")),
    ("responder", ("responder", "cevaplayici", "citations", "spoken")),
    ("planner", ("planner", "planlayici", "steps", "goal", "adim")),
    ("router", ("router", "yonlendirici", "route")),
)

_BROWSER_HINTS = (
    "sayfa",
    "ozetle",
    "form",
    "doldur",
    "gonder",
    "tikla",
    "buton",
    "sekme",
    "sepete",
    "satin al",
    "siparis",
)
_SEARCH_HINTS = ("ara", "fiyat", "haber", "guncel", "nedir", "kac")
_FORM_HINTS = ("form", "doldur", "gonder", "tikla", "satın al", "satin al", "ode", "onayla")

_FINISH_TOOL = "finish"


class FakeLLM:
    """Duck-typed :class:`~backend.llm.LLMClient` that never touches the network.

    Every answer is derived from the system/user text the agents already send,
    so the pipeline stays deterministic while still exercising every branch.
    """

    def __init__(self, model: str = "fake-offline") -> None:
        self.model = model
        self._exec_turns = 0
        self._exec_plan: list[str] | None = None

    @property
    def configured(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    # -- strict JSON agents ----------------------------------------------
    async def chat_json(
        self,
        system: str,
        messages: Sequence[Frame],
        temperature: float = 0.0,
    ) -> tuple[Frame, Usage]:
        raw = _all_text(messages)
        kind = _agent_kind(system, raw)
        if kind == "router":
            route, confidence, why = _pick_route(_fold(_last_user_content(messages) or raw))
            payload: Frame = {"route": route, "confidence": confidence, "why": why}
        elif kind == "planner":
            payload = _plan_payload(raw)
        elif kind == "judge":
            payload = {
                "scores": {"correct": 5, "complete": 5, "safe": 5},
                "verdict": "pass",
                "feedback": "Adımlar hedefi karşılıyor ve sonuç araç kanıtlarına dayanıyor.",
            }
        elif kind == "responder":
            payload = _answer_payload(raw)
        else:
            payload = {"note": "FakeLLM: tanınmayan ajan, boş cevap."}
        return payload, _fake_usage(self.model)

    # -- executor function calling ---------------------------------------
    async def chat_tools(
        self,
        system: str,
        messages: Sequence[Frame],
        tools: Sequence[Frame],
        temperature: float = 0.2,
    ) -> tuple[ToolMessage, Usage]:
        names = _tool_names(tools)
        raw = _all_text(messages)
        if self._exec_plan is None:
            # Freeze the plan on the first turn: later turns see the snapshot in
            # their own context and must not switch scenario halfway through.
            # Only the goal is scanned — the executor's own instructions contain
            # words like "doldur" and would otherwise fake a form request.
            self._exec_plan = _executor_plan(names, _goal_of(raw))
        turn = self._exec_turns
        self._exec_turns += 1

        if turn >= len(self._exec_plan) or self._exec_plan[turn] == _FINISH_TOOL:
            return _finish_message(raw), _fake_usage(self.model)

        name = self._exec_plan[turn]
        call = ToolCall(id=f"call_{turn + 1}", name=registry.llm_name(name), args=_args_for(name, raw))
        return ToolMessage(content="", tool_calls=[call]), _fake_usage(self.model)


def _fake_usage(model: str) -> Usage:
    return Usage(
        model=model,
        prompt_tokens=320,
        completion_tokens=64,
        total_tokens=384,
        latency_ms=12,
        attempts=1,
        finish_reason="stop",
    )


def _agent_kind(system: str, raw: str) -> str:
    """Guess which agent is asking, from the prompt markers it is known to carry."""

    haystack = _fold(f"{system}\n{raw}")
    best = "generic"
    best_hits = 0
    for kind, markers in _MARKERS:
        hits = sum(1 for marker in markers if marker in haystack)
        if hits > best_hits:
            best, best_hits = kind, hits
    return best


def _pick_route(folded: str) -> tuple[str, float, str]:
    if any(hint in folded for hint in _BROWSER_HINTS):
        return (
            "browser_task",
            0.82,
            "İstek açık sekmede okuma veya tıklama gerektiriyor.",
        )
    if any(hint in folded for hint in _SEARCH_HINTS):
        return "web_search", 0.78, "Güncel bilgi aranıyor; sayfada işlem gerekmeyebilir."
    return "chat", 0.9, "Sohbet isteği; tarayıcı işlemi gerekmiyor."


def _plan_payload(raw: str) -> Frame:
    goal = _field(raw, "Kullanıcı isteği") or _field(raw, "Hedef") or "Kullanıcı isteğini karşıla"
    return {
        "goal": _clip(goal, 160),
        "steps": [
            {
                "id": 1,
                "action": "Aktif sekmeyi incele ve güncel öğeleri çıkar",
                "why": "Etkileşimden önce güncel referanslar gerekiyor.",
            },
            {
                "id": 2,
                "action": "Elde edilen sonucu kısa bir özete indir",
                "why": "Kullanıcıya kanıta dayalı cevap vermek için.",
            },
        ],
    }


def _answer_payload(raw: str) -> Frame:
    goal = _field(raw, "Hedef") or _field(raw, "Kullanıcı isteği") or "belirtilmedi"
    route = (_field(raw, "Rota") or "chat").strip().lower()
    url = _first_url(raw)
    clicked = "browser.click" in raw

    if route == "browser_task":
        lines = [
            f"“{_clip(goal, 140)}” isteği için aktif sekmeyi inceledim.",
            "",
            "- Sayfada bir arama kutusu, “Ara” düğmesi ve üç ürün kartı var.",
            "- Ürünler: Kablosuz Kulaklık 1.299 TL, Mekanik Klavye 899 TL, USB-C Hub 549 TL.",
            "- Kargo: 500 TL üzeri siparişlerde ücretsiz; iade süresi 14 gün.",
        ]
        if "kullanıcı reddetti" in raw:
            lines.append("- Riskli adım kullanıcı tarafından reddedildi; tıklama yapılmadı.")
        elif clicked:
            lines.append("- “Siparişi tamamla” düğmesine yalnızca onay alındıktan sonra tıklandı.")
        lines.append("")
        lines.append("Not: Bu cevap `--offline` sahte modelinden geliyor; sayfa verisi CLI'nin sahte köprüsünden alındı.")
        answer = "\n".join(lines)
        spoken = f"Sayfayı inceledim; “{_clip(goal, 80)}” isteği için öğeleri ve ürün listesini çıkardım."
    elif route == "web_search":
        answer = (
            f"“{_clip(goal, 140)}” için arama adımı çalıştırıldı.\n\n"
            "- Sonuçlar sahte köprüden geldiği için gerçek kaynak içeriği yok.\n"
            "- Gerçek cevap için `--offline` olmadan DEEPSEEK_API_KEY ile çalıştır."
        )
        spoken = f"“{_clip(goal, 80)}” için arama yaptım; çevrimdışı olduğum için yalnızca iskelet sonuç var."
    else:
        answer = (
            f"Merhaba! “{_clip(goal, 140)}” mesajını aldım.\n\n"
            "Şu an `--offline` sahte modeli ile çalışıyorum: akış (router → planner → executor → "
            "judge → responder) gerçek, fakat cevabı üreten model değil. Gerçek cevap için "
            "`backend/.env` içine `DEEPSEEK_API_KEY` yazıp `--offline` bayrağını kaldır."
        )
        spoken = "Merhaba! Çevrimdışı sahte modelle çalışıyorum; isteğini aldım."

    citations: list[Frame] = []
    if url:
        citations.append({"title": _title_from_url(url), "url": url})
    return {"answer": answer, "spoken": spoken, "citations": citations}


def _tool_names(tools: Sequence[Frame]) -> list[str]:
    names: list[str] = []
    for schema in tools:
        function = schema.get("function") if isinstance(schema, dict) else None
        raw = function.get("name") if isinstance(function, dict) else None
        if isinstance(raw, str) and raw:
            names.append(registry.from_llm_name(raw))
    return names


def _executor_plan(names: Sequence[str], raw: str) -> list[str]:
    """Deterministic tool sequence for the fake executor."""

    folded = _fold(raw)
    if any(hint in folded for hint in _FORM_HINTS) and "browser.click" in names:
        wanted = ["browser.snapshot", "browser.click"]
    elif "browser.read" in names:
        wanted = ["browser.snapshot", "browser.read"]
    elif "web.search" in names:
        wanted = ["web.search", "browser.read"]
    else:
        wanted = []
    return [*[name for name in wanted if name in names], _FINISH_TOOL]


def _args_for(name: str, raw: str) -> Frame:
    why = "Planlanan adımı uyguluyorum."
    if name == "browser.snapshot":
        return {"why": "Tıklama veya okuma öncesi güncel öğe referanslarını alıyorum."}
    if name == "browser.read":
        return {"why": "Sayfa metnini okuyup istenen özeti çıkarıyorum."}
    if name == "browser.click":
        return {"ref": RISKY_REF, "why": "Kullanıcı formu göndermek istedi; onay düğmesine tıklıyorum."}
    if name == "browser.type":
        return {"ref": 3, "text": "kablosuz kulaklık", "why": "Arama kutusunu dolduruyorum."}
    if name == "browser.scroll":
        return {"direction": "down", "why": "Aranan öğeyi görebilmek için sayfayı kaydırıyorum."}
    if name == "browser.navigate":
        return {"url": DEFAULT_URL, "why": "İstenen adrese gidiyorum."}
    if name == "web.search":
        return {"query": _clip(_goal_of(raw), 120), "why": "Güncel bilgi için arama yapıyorum."}
    return {"why": why}


def _executor_goal(raw: str) -> str:
    """The user's own words inside an agent prompt blob (never its instructions)."""

    return (
        _field(raw, "Kullanıcı hedefi")
        or _field(raw, "Kullanıcı isteği")
        or _field(raw, "Hedef")
    )


def _goal_of(raw: str) -> str:
    goal = _executor_goal(raw)
    if goal:
        return goal
    first = next((line for line in raw.splitlines() if line.strip()), "")
    return _clip(first.lstrip("# ").strip(), 120)


def _finish_message(raw: str) -> ToolMessage:
    goal = _goal_of(raw)
    summary = f"Kanıt toplandı; “{_clip(goal, 120)}” isteği için özet çıkarıldı."
    why = "Yeterli kanıt toplandı, iş bitti."
    return ToolMessage(
        content=summary,
        tool_calls=[ToolCall(id="call_finish", name=_FINISH_TOOL, args={"summary": summary, "why": why})],
    )


# ---------------------------------------------------------------------------
# LocalBridge: answers every browser.* call instantly with mock data
# ---------------------------------------------------------------------------
#: Answers accepted as "yes" at the terminal confirmation prompt.
_YES_ANSWERS = frozenset({"e", "evet", "y", "yes", "t", "true", "1", "on", "tamam"})


class LocalBridge(BrowserBridge):
    """In-process stand-in for the extension side of the WebSocket bridge.

    `call()` never waits for a socket, so the 20 s tool timeout is never hit;
    `request_confirm()` sends the real `need_confirm` frame and then either
    approves it (``auto_yes``, CLI `-y`), asks the operator at the terminal, or —
    when there is no interactive terminal — rejects it immediately.  A piped or
    CI run therefore never blocks for `confirm_timeout_s`.
    """

    def __init__(
        self,
        send: SendFn,
        *,
        auto_yes: bool = False,
        tab: Frame | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(send, request_id)
        self.auto_yes = auto_yes
        self.tab = tab or {}
        self._emit = send

    async def call(self, name: str, args: Frame, timeout: float | None = None) -> Frame:
        return _fake_tool_result(name, args or {}, self.tab)

    async def request_confirm(
        self,
        call_id: str,
        summary: str,
        tool: str,
        args: Frame,
        timeout: float | None = None,
    ) -> bool:
        await self._emit(
            {
                "type": "need_confirm",
                "call_id": call_id,
                "request_id": self.request_id,
                "summary": summary,
                "tool": tool,
                "args": args or {},
            }
        )
        if self.auto_yes:
            print("[confirm] Onay kapısı otomatik onaylandı (-y) — beklenmeden devam ediliyor.")
            return True

        approved = await self._ask_terminal(timeout)
        if not approved:
            print(
                "[confirm] Onay verilmedi (ret, süre aşımı ya da etkileşimsiz oturum) — adım çalıştırılmadı.",
                flush=True,
            )
        return approved

    async def _ask_terminal(self, timeout: float | None) -> bool:
        """Ask the operator; fail closed when there is no interactive terminal."""

        stdin = getattr(sys, "stdin", None)
        if stdin is None or not getattr(stdin, "isatty", lambda: False)():
            print(
                "[confirm] Etkileşimli terminal yok; onay reddedildi "
                "(otomatik onay için -y kullan).",
                flush=True,
            )
            return False

        budget = float(timeout if timeout is not None else get_settings().confirm_timeout_s)
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(input, "[confirm] Onaylıyor musun? [e/H]: "), timeout=max(0.0, budget)
            )
        except asyncio.TimeoutError:
            print(f"\n[confirm] {budget:.0f} sn içinde yanıt gelmedi; reddedilmiş sayılıyor.", flush=True)
            return False
        except (EOFError, OSError):
            print("\n[confirm] Girdi okunamadı; reddedilmiş sayılıyor.", flush=True)
            return False
        return str(answer).strip().lower() in _YES_ANSWERS


def _fake_tool_result(name: str, args: Frame, tab: Frame) -> Frame:
    url = str(tab.get("url") or DEFAULT_URL)
    title = str(tab.get("title") or DEFAULT_TITLE)

    if name == "browser.snapshot":
        return {
            "ok": True,
            "data": {
                "source": "browser",
                "url": url,
                "title": title,
                "elements": [dict(element) for element in _FAKE_ELEMENTS],
                "text_excerpt": _clip(_FAKE_PAGE_TEXT, 400),
            },
        }
    if name == "browser.read":
        return {
            "ok": True,
            "data": {"url": url, "title": title, "text": _FAKE_PAGE_TEXT[:4000]},
        }
    if name == "browser.navigate":
        return {
            "ok": True,
            "data": {"url": str(args.get("url") or url), "title": title},
        }
    if name in {"browser.click", "browser.type", "browser.scroll", "browser.highlight"}:
        return {"ok": True, "data": {"ok": True, "tool": name, "args": args}}
    # Anything that is not a browser.* tool never reaches the extension in
    # production; here it must still answer instantly instead of timing out.
    logger.info("LocalBridge: %s için sahte yanıt üretildi", name)
    return {"ok": True, "data": {"stub": True, "tool": name, "note": "CLI sahte köprüsü"}}


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------
def _format_trace(step: Frame, *, verbose: bool) -> list[str]:
    agent = str(step.get("agent") or "system")
    decision = str(step.get("decision") or "")
    verdict = step.get("verdict")
    if agent == "judge" and verdict and str(verdict) not in decision:
        decision = str(verdict)
    why = str(step.get("why") or "")
    tool = step.get("tool") if isinstance(step.get("tool"), dict) else None

    head = f"[trace] {agent} | {decision}"
    if tool and tool.get("name"):
        head += f" | araç: {tool.get('name')}"
    if why:
        head += f" | neden: {why}"
    scores = step.get("scores") if isinstance(step.get("scores"), dict) else None
    if scores:
        head += " | skorlar " + " ".join(f"{key}={value}" for key, value in scores.items())
    latency = int(step.get("latency_ms") or 0)
    tokens = step.get("tokens")
    head += f" ({latency} ms" + (f", {tokens} tok" if tokens is not None else "") + ")"

    lines = [head]
    if verbose:
        if tool:
            args = json.dumps(tool.get("args") or {}, ensure_ascii=False)
            lines.append(f"    araç argümanları: {args}")
        evidence = step.get("evidence") or []
        if evidence:
            lines.append("    kanıt: " + ", ".join(str(item) for item in evidence))
        if step.get("confidence") is not None:
            lines.append(f"    güven: {step['confidence']}")
        lines.append(f"    ts: {step.get('ts')}")
    return lines


def _make_sender(args: argparse.Namespace, state: Frame) -> SendFn:
    """Print every outgoing frame as a readable Turkish line."""

    async def send(frame: Frame) -> None:
        kind = str(frame.get("type") or "?")
        if args.json:
            print(json.dumps(frame, ensure_ascii=False), flush=True)

        if kind == "trace":
            step = frame.get("step") if isinstance(frame.get("step"), dict) else {}
            for line in _format_trace(step, verbose=args.trace):
                print(line, flush=True)
            return

        if kind == "tool_call":
            payload = json.dumps(frame.get("args") or {}, ensure_ascii=False)
            print(f"[tool_call] {frame.get('name')} {payload}", flush=True)
            return

        if kind == "need_confirm":
            print(
                f"[need_confirm] {frame.get('summary')} (araç: {frame.get('tool')})",
                flush=True,
            )
            return

        if kind == "final":
            state["final"] = True
            print(f"[final] {frame.get('answer') or ''}", flush=True)
            spoken = str(frame.get("spoken") or "").strip()
            if spoken:
                print(f"[spoken] {spoken}", flush=True)
            for citation in frame.get("citations") or []:
                if isinstance(citation, dict):
                    print(f"[kaynak] {citation.get('title') or ''} — {citation.get('url') or ''}", flush=True)
            return

        if kind == "notify":
            print(f"[notify] {frame.get('text') or ''}", flush=True)
            return

        if kind == "error":
            state["error"] = str(frame.get("message") or "bilinmeyen hata")
            print(f"[error] {state['error']}", flush=True)
            return

        print(f"[frame] {json.dumps(frame, ensure_ascii=False)}", flush=True)

    return send


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli",
        description="Orchestrator'ı extension olmadan terminalde çalıştırır.",
    )
    parser.add_argument("question", nargs="?", default="", help="Kullanıcının sorusu (Türkçe).")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="LLM anahtarı olmadan çalış: sahte LLM ve sahte tarayıcı köprüsü kullanılır.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Her trace adımını ayrıntılı yazdır (varsayılan: tek satır özet).",
    )
    parser.add_argument("--json", action="store_true", help="Ham frame'leri de JSON olarak yazdır.")
    parser.add_argument("--tab-url", default="", help="Aktif sekme adresini taklit et.")
    parser.add_argument("--tab-title", default="", help="Aktif sekme başlığını taklit et.")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Onay kapısını otomatik onayla (riskli senaryoyu 60 sn beklemeden test eder).",
    )
    return parser.parse_args(argv)


def _force_utf8() -> None:
    """Turkish characters must survive the Windows console."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - unusual stream
            logger.info("Akış kodlaması ayarlanamadı: %r", stream)


def _build_tab(args: argparse.Namespace) -> Frame | None:
    if not args.tab_url and not args.tab_title:
        return None
    return {"id": 1, "url": args.tab_url, "title": args.tab_title}


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()

    if args.offline:
        client: Any = FakeLLM()
    else:
        client = get_llm()
        if not client.configured:
            print(NO_KEY_MESSAGE, flush=True)
            return 2

    try:
        from .orchestrator import Orchestrator
    except Exception as exc:  # module still missing / broken
        logger.info("Orchestrator içe aktarılamadı: %s", exc)
        print(NO_ORCHESTRATOR_MESSAGE, flush=True)
        return 2

    request_id = f"r_{secrets.token_hex(3)}"
    state: Frame = {"final": False, "error": None}
    send = _make_sender(args, state)
    tab = _build_tab(args)

    print(f"[cli] soru: {args.question}")
    print(f"[cli] istek: {request_id} | mod: {'offline (sahte LLM)' if args.offline else 'gerçek LLM'}")
    if tab:
        print(f"[cli] sekme: “{tab.get('title') or '(başlıksız)'}” — {tab.get('url') or '(adres yok)'}")

    # SQLite must be able to open the file before TraceStore.init() runs.
    ensure_data_dir()
    store = TraceStore(str(settings.db_path))
    bridge = LocalBridge(send, auto_yes=args.yes, tab=tab, request_id=request_id)
    orchestrator = Orchestrator(send, store=store, bridge=bridge, client=client)

    frame: Frame = {
        "type": "user_msg",
        "id": request_id,
        "text": args.question,
        "source": "text",
        "tab": tab,
    }

    try:
        await orchestrator.start()
        await orchestrator.handle_frame(frame)
        await orchestrator.wait_idle(timeout=settings.request_budget_s + 20)
    finally:
        await orchestrator.close()
        try:
            await client.close()
        except Exception as exc:  # pragma: no cover - fake/real client teardown
            logger.info("LLM istemcisi kapatılamadı: %s", exc)

    if state["error"]:
        return 1
    if state["final"]:
        return 0
    print(f"[cli] {settings.request_budget_s + 20:.0f} sn içinde final alınamadı.", flush=True)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.trace else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    if not args.question.strip():
        print(USAGE_HINT, flush=True)
        return 2
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n[cli] Kullanıcı iptal etti.", flush=True)
        return 130


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
