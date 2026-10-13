"""Fake OpenAI-compatible LLM server + fake Brave Search, scripted per scenario.

The backend talks to it exactly as it would to DeepSeek:

* JSON-mode agents (router / planner / judge / responder) -> `response_format=json_object`
* executor -> function calling (`tools=[...]`, tool names like `browser__click`)

Everything is deterministic.  The scenario is derived from the *user text* that
travels through the whole pipeline (router message -> planner "Kullanici
istegi" -> plan.goal -> executor "Kullanici hedefi" -> judge "Hedef" ->
responder "Hedef"), and the executor's next step is derived from the HISTORY
block it is shown.  So the fake needs no per-connection state and also reveals
integration bugs: if a tool result never reaches the HISTORY block, the script
cannot continue the way a real model could not either.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

#: (scenario name, substring of the casefolded user text), first match wins.
KEYWORDS: tuple[tuple[str, str], ...] = (
    ("bad_json", "bozukjson"),
    ("llm_500", "llmhata"),
    ("slow", "yavaş"),
    ("tool_timeout", "zaman aşımı"),
    ("disconnect", "bağlantı kop"),
    ("revise", "revize"),
    ("ask_user", "belirsiz"),
    ("endless", "sonsuz"),
    ("desktop", "masaüstü"),
    ("order", "siparişi tamamla"),
    ("form", "formu doldur"),
    ("shop_search", "klavye"),
    ("web", "kulaklı"),
    ("summarize", "özetle"),
    ("reminder", "hatırlat"),
    ("memory_save", "unutma"),
    ("memory_search", "adı ne"),
)

ROUTES = {
    "chat": "chat", "bad_json": "chat", "llm_500": "chat",
    "web": "web_search",
    "reminder": "personal", "memory_save": "personal", "memory_search": "personal",
    "desktop": "desktop_task",
}


def classify(text: str) -> str:
    folded = (text or "").casefold()
    for name, needle in KEYWORDS:
        if needle in folded:
            return name
    return "chat"


def route_for(scenario: str) -> str:
    return ROUTES.get(scenario, "browser_task")


# --------------------------------------------------------------------------
# prompt parsing
# --------------------------------------------------------------------------

def detect_role(system: str, has_tools: bool) -> str:
    head = system[:400]
    if has_tools or "Executor (" in head:
        return "executor"
    if "Judge (" in head:
        return "judge"
    if "Planlayıcı" in head:
        return "planner"
    if "Cevaplayıcı" in head:
        return "responder"
    if "yönlendiricisin" in system[:600]:
        return "router"
    return "unknown"


def _section(text: str, start: str, end: str | None) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i) if end else -1
    return text[i:j if j >= 0 else len(text)].strip("\n")


@dataclass
class Hist:
    tool: str
    args: str
    status: str  # ok | error | rejected
    body: str


def parse_history(user: str) -> list[Hist]:
    block = _section(user, "## HISTORY\n", "\n\n## SNAPSHOT")
    out: list[Hist] = []
    for line in block.splitlines():
        m = re.match(r"- ([\w.]+)\((.*?)\) -> (.*)", line)
        if not m:
            continue
        rest = m.group(3)
        if rest.startswith("kullanıcı reddetti"):
            status = "rejected"
        elif rest.startswith("HATA"):
            status = "error"
        else:
            status = "ok"
        out.append(Hist(m.group(1), m.group(2), status, rest))
    return out


def snapshot_refs(user: str) -> dict[str, int]:
    """name -> ref from the rendered SNAPSHOT block of the executor prompt."""

    block = _section(user, "## SNAPSHOT\n", "\n\n## Görev")
    return {m.group(2): int(m.group(1)) for m in re.finditer(r'^\[(\d+)\] \S+ "(.*?)"', block, re.M)}


def _last_user(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


# --------------------------------------------------------------------------
# executor script
# --------------------------------------------------------------------------

Action = tuple  # ("tool", name, args) | ("finish", summary)


def _called(hist: list[Hist], tool: str, status: str | None = None) -> bool:
    return any(h.tool == tool and (status is None or h.status == status) for h in hist)


def executor_action(scenario: str, text: str, user: str) -> Action:
    hist = parse_history(user)
    names = [h.tool for h in hist]

    if any(h.status == "rejected" for h in hist):
        return ("finish", "Kullanıcı işlemi reddetti, bu yüzden işlem yapılmadı.")

    if scenario in ("summarize", "slow", "tool_timeout", "disconnect"):
        if "browser.read" not in names:
            return ("tool", "browser.read", {})
        if _called(hist, "browser.read", "error"):
            return ("finish", "Sayfa okunamadı: araç yanıt vermedi (hata).")
        return ("finish", "Sayfa bir alışveriş sitesidir; mekanik klavyeler ve fiyatları listeler.")

    if scenario == "revise":
        if "judge_feedback" in names:
            return ("finish", "Düzeltilmiş sonuç: sayfa özeti tamamlandı.")
        if "browser.read" not in names:
            return ("tool", "browser.read", {})
        return ("finish", "İlk taslak sonuç.")

    if scenario == "ask_user":
        if "browser.read" not in names:
            return ("tool", "browser.read", {})
        return ("finish", "Sonuç belirsiz, kullanıcı onayı gerekiyor.")

    if scenario == "endless":
        return ("tool", "browser.scroll", {"direction": "down"})

    if scenario == "web":
        if "web.search" not in names:
            return ("tool", "web.search", {"query": "en iyi kablosuz kulaklık", "count": 3})
        if _called(hist, "web.search", "error"):
            return ("finish", "Arama başarısız oldu.")
        urls = re.findall(r"https?://[^\s\"',}]+", " ".join(h.body for h in hist))
        listing = ", ".join(dict.fromkeys(urls)) or "(arama sonucu görünmüyor)"
        return ("finish", f"Arama sonuçları: {listing}")

    if scenario == "shop_search":
        if "browser.snapshot" not in names:
            return ("tool", "browser.snapshot", {})
        if "browser.type" not in names:
            return ("tool", "browser.type", {"ref": 7, "text": "mekanik klavye"})
        if "browser.read" not in names:
            return ("tool", "browser.read", {})
        if "browser.highlight" not in names:
            return ("tool", "browser.highlight", {"ref": 21})
        return ("finish", "İlk 3 fiyat: 1.499 TL, 1.299 TL, 999 TL.")

    if scenario == "order":
        if "browser.snapshot" not in names:
            return ("tool", "browser.snapshot", {})
        if "browser.click" not in names:
            return ("tool", "browser.click", {"ref": 52})
        if _called(hist, "browser.click", "error"):
            return ("finish", "Tıklama başarısız oldu.")
        return ("finish", "Sipariş tamamlandı.")

    if scenario == "form":
        if "browser.snapshot" not in names:
            return ("tool", "browser.snapshot", {})
        typed = [h for h in hist if h.tool == "browser.type"]
        if len(typed) == 0:
            return ("tool", "browser.type", {"ref": 5, "text": "Ayşe Yılmaz"})
        if len(typed) == 1:
            return ("tool", "browser.type", {"ref": 6, "text": "ayse@example.com", "submit": True})
        return ("finish", "Form dolduruldu ve gönderildi.")

    if scenario == "reminder":
        if "reminders.add" not in names:
            due = (datetime.now().astimezone() + timedelta(seconds=3)).isoformat(timespec="seconds")
            return ("tool", "reminders.add", {"text": "toplantı 10:00", "due_iso": due})
        if _called(hist, "reminders.add", "error"):
            return ("finish", "Hatırlatma eklenemedi.")
        return ("finish", "Hatırlatma eklendi: toplantı 10:00.")

    if scenario == "memory_save":
        if "memory.save" not in names:
            return ("tool", "memory.save", {"text": "Kedimin adı Pamuk", "tags": ["kedi"]})
        return ("finish", "Kaydettim: kedinin adı Pamuk.")

    if scenario == "memory_search":
        if "memory.search" not in names:
            return ("tool", "memory.search", {"query": "kedi"})
        body = " ".join(h.body for h in hist if h.tool == "memory.search")
        if "Pamuk" in body:
            return ("finish", "Kedinin adı Pamuk.")
        return ("finish", "Hafızada böyle bir kayıt bulunamadı.")

    if scenario == "desktop":
        if "desktop.snapshot" not in names:
            return ("tool", "desktop.snapshot", {})
        if _called(hist, "desktop.snapshot", "error"):
            return ("finish", "Masaüstü görüntüsü alınamadı.")
        refs = snapshot_refs(user)
        target = next((ref for name, ref in refs.items() if "deneme" in name), None)
        if "desktop.click" not in names:
            return ("tool", "desktop.click", {"ref": target if target is not None else 1})
        if "desktop.type" not in names:
            return ("tool", "desktop.type", {"text": "Merhaba"})
        if names.count("desktop.snapshot") < 2:
            return ("tool", "desktop.snapshot", {})
        return ("finish", "Metin düzenleyiciye 'Merhaba' yazıldı.")

    return ("finish", "Yapılacak bir şey yok.")


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

@dataclass
class LLMCall:
    role: str
    scenario: str
    system: str
    user: str
    ts: float = field(default_factory=time.time)


class FakeLLMState:
    def __init__(self) -> None:
        self.calls: list[LLMCall] = []
        self.brave_queries: list[str] = []
        self.http_500_count = 0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.brave_queries.clear()
            self.http_500_count = 0

    def roles(self) -> list[str]:
        return [c.role for c in list(self.calls)]

    def calls_for(self, role: str) -> list[LLMCall]:
        return [c for c in list(self.calls) if c.role == role]


def _completion(content: str | None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake-llm",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _json_reply(obj: dict[str, Any]) -> dict[str, Any]:
    return _completion(json.dumps(obj, ensure_ascii=False))


def _spoken(text: str) -> str:
    text = re.sub(r"https?://\S+|[*_#`>|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return " ".join(parts[:2]) or "Tamamdır."


def build_app(state: FakeLLMState) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    # -- fake Brave Search ------------------------------------------------
    @app.get("/brave/res/v1/web/search")
    async def brave(q: str = "", count: int = 5) -> dict[str, Any]:
        state.brave_queries.append(q)
        items = [
            {
                "title": f"<strong>{name}</strong> incelemesi",
                "url": f"https://reviews.example.com/{slug}",
                "description": f"{name} ses kalitesi ve pil ömrü testi.",
            }
            for name, slug in (("Sony WH-1000XM6", "sony"), ("Bose QC Ultra", "bose"), ("Apple AirPods Max", "airpods"))
        ]
        return {"web": {"results": items[:count]}}

    # -- chat completions -------------------------------------------------
    @app.post("/chat/completions")
    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        body = await request.json()
        messages: list[dict[str, Any]] = body.get("messages") or []
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user = _last_user(messages)
        tools = body.get("tools") or []
        role = detect_role(system if isinstance(system, str) else "", bool(tools))

        text = _goal_text(role, user)
        scenario = classify(text)
        state.calls.append(LLMCall(role=role, scenario=scenario, system=str(system), user=user))

        if scenario == "llm_500" and role in ("router", "responder"):
            state.http_500_count += 1
            return JSONResponse({"error": {"message": "fake upstream failure", "type": "server_error"}}, status_code=500)
        if scenario == "bad_json" and role == "router":
            return JSONResponse(_completion("bu bir JSON değil {{{"))

        if role == "router":
            route = route_for(scenario)
            return JSONResponse(_json_reply({"route": route, "confidence": 0.93, "why": f"Senaryo {scenario}: {route} rotası seçildi."}))

        if role == "planner":
            return JSONResponse(
                _json_reply(
                    {
                        "goal": text,
                        "steps": [
                            {"id": 1, "action": "Gerekli araçları sırayla çağır", "why": "İstek bunu gerektiriyor."},
                            {"id": 2, "action": "Sonucu özetle", "why": "Kullanıcıya cevap ver."},
                        ],
                    }
                )
            )

        if role == "executor":
            action = executor_action(scenario, text, user)
            if action[0] == "tool":
                args = dict(action[2])
                args["why"] = f"Plan gereği {action[1]} çağrılıyor."
                call = {
                    "id": f"call_{len(state.calls)}",
                    "type": "function",
                    "function": {"name": action[1].replace(".", "__"), "arguments": json.dumps(args, ensure_ascii=False)},
                }
                return JSONResponse(_completion(None, [call]))
            call = {
                "id": f"call_{len(state.calls)}",
                "type": "function",
                "function": {
                    "name": "finish",
                    "arguments": json.dumps({"summary": action[1], "why": "Hedefe ulaşıldı."}, ensure_ascii=False),
                },
            }
            return JSONResponse(_completion(None, [call]))

        if role == "judge":
            steps = _section(user, "## Yapılan adımlar\n", "\n\n## Mevcut durum")
            if scenario == "revise" and "judge_feedback" not in steps:
                return JSONResponse(_json_reply({"scores": {"correct": 2, "complete": 2, "safe": 5}, "verdict": "revise", "feedback": "Cevap eksik, tekrar dene."}))
            if scenario == "ask_user" and "kullanici_cevabi" not in steps:
                return JSONResponse(_json_reply({"scores": {"correct": 3, "complete": 3, "safe": 4}, "verdict": "ask_user", "feedback": "Devam edeyim mi?"}))
            return JSONResponse(_json_reply({"scores": {"correct": 5, "complete": 4, "safe": 5}, "verdict": "pass", "feedback": "Hedefle tutarlı."}))

        if role == "responder":
            return JSONResponse(_json_reply(_responder_reply(scenario, user)))

        return JSONResponse(_json_reply({}))

    return app


def _goal_text(role: str, user: str) -> str:
    """Recover the original user text from whichever agent prompt we got."""

    if role == "router":
        return user
    if role == "planner":
        m = re.search(r"Kullanıcı isteği: (.*)\n", user)
        return m.group(1) if m else user
    if role == "executor":
        return _section(user, "## Kullanıcı hedefi\n", "\n\n## Plan")
    if role == "judge":
        return _section(user, "## Hedef\n", "\n\n## Plan")
    if role == "responder":
        m = re.search(r"Hedef: (.*)\n", user)
        return m.group(1) if m else user
    return user


def _responder_reply(scenario: str, user: str) -> dict[str, Any]:
    m = re.search(r"Sonuç özeti: (.*)", user)
    summary = (m.group(1).strip() if m else "") or ""
    if scenario in ("chat", "bad_json"):
        return {"answer": "Merhaba! Size nasıl yardımcı olabilirim?", "spoken": "Merhaba! Size nasıl yardımcı olabilirim?", "citations": []}
    if scenario == "llm_500":
        return {"answer": "x", "spoken": "x", "citations": []}
    if summary in ("(özet yok)", ""):
        summary = "İşlem tamamlandı."
    urls = list(dict.fromkeys(re.findall(r"https?://[^\s\"',}\])]+", user)))
    citations = [{"title": u.split("//", 1)[1].split("/")[0], "url": u, "ref": None} for u in urls[:3]]
    answer = f"**Sonuç:** {summary}"
    if scenario == "web":
        answer += "\n\n| Ürün | Kaynak |\n|---|---|\n" + "\n".join(f"| {c['title']} | {c['url']} |" for c in citations)
    return {"answer": answer, "spoken": _spoken(summary), "citations": citations}


# --------------------------------------------------------------------------
# in-process runner
# --------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class FakeLLMServer:
    """uvicorn in a daemon thread; `.state` is shared with the test code."""

    def __init__(self) -> None:
        self.state = FakeLLMState()
        self.port = free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(build_app(self.state), host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, name="fake-llm", daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.time() + 15
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake LLM server did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


if __name__ == "__main__":  # manual debugging: python -m scripts.e2e.fake_llm
    srv = FakeLLMServer()
    srv.start()
    print(srv.base_url)
    try:
        asyncio.get_event_loop_policy()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
