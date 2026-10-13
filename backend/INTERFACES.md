# K3 Frozen Interfaces (read this before writing any file)

Bu doküman K3 alanındaki (backend çekirdeği, ajanlar, orchestrator) **donmuş**
arayüzleri tanımlar. Yazdığın her modül bu imzalara uymak zorunda. Sözleşmeyi
(ORTAK BAĞLAM §6 WebSocket protokolü, §7 tool sözleşmesi) değiştirmek yasak.

Kod ve tanımlayıcılar **İngilizce**, kullanıcıya görünen metinler **Türkçe**.
Python: type hint + async + `logging.getLogger(__name__)`. `any` yok.

---

## 1. Repo ve sahiplik

```
assistant/
├─ extension/                 (K1: panel/UI, K2: background/content/bridge)
├─ backend/
│  ├─ config.py               K3  (YAZILDI - değiştirme)
│  ├─ main.py                 K3  (FastAPI app, /health, /ws, CORS, router include)
│  ├─ orchestrator.py         K3  (akış, onay kapısı, limitler, cancel)
│  ├─ bridge.py               K3  (YAZILDI - değiştirme)
│  ├─ llm.py                  K3  (YAZILDI - değiştirme)
│  ├─ trace.py                K3  (YAZILDI - değiştirme)
│  ├─ cli.py                  K3  (extension'sız çalıştırma)
│  ├─ agents/base.py          K3  (YAZILDI - değiştirme)
│  ├─ agents/{router,planner,executor,judge,responder}.py   K3
│  ├─ prompts/*.md            K3  (Türkçe)
│  ├─ tools/registry.py       K3  (YAZILDI - değiştirme)
│  ├─ tools/browser.py        K3  (7 browser.* tool'u, bridge sarmalı)
│  ├─ tools/{desktop,search,memory,reminders}.py   K4 / K5
│  ├─ voice/{stt,tts}.py      K5
│  ├─ requirements.txt        K3  (herkes satır ekler)
│  └─ .env.example            K3
├─ shared/fixtures/session_example.jsonl   K3
└─ scripts/{mock_ws.py,check_contract.py,smoke_test.py}   K3
```

Başkasının dosyasını **yazma**. K4/K5 modülleri (`tools/desktop.py`,
`tools/search.py`, `tools/memory.py`, `tools/reminders.py`, `voice/stt.py`,
`voice/tts.py`) henüz **yok**: onlara bağımlılık `try/except ImportError` +
`logger.info` ile opsiyonel olmalı. Hiçbir import, modül yok diye uygulamayı
düşürmemeli.

---

## 2. Ortam

- Python 3.13 kurulu (3.11+ sözdizimi serbest), `fastapi`, `uvicorn`, `openai`,
  `pydantic`, `httpx`, `python-dotenv` **zaten kurulu**.
- Windows + PowerShell. Çalıştırma dizini: `C:\Users\anıl\Desktop\evren\assistant`.
- Test: `python -m compileall backend` ve gerekiyorsa
  `python -c "from backend.main import app"` (cwd = `assistant/`).
- Backend portu 8000, mock sunucu portu **8001**.

---

## 3. config.py (yazıldı)

`from ..config import get_settings, now_iso, now_local, BACKEND_DIR, PROMPTS_DIR, ensure_data_dir`

`Settings` alanları: `deepseek_api_key, deepseek_base_url, llm_model, brave_api_key,
stt_model, tts_voice, timezone, cua_enabled, desktop_parser, db_path, host, port,
llm_timeout_s, llm_temperature_chat, tool_timeout_s (20.0), confirm_timeout_s (60.0),
max_tool_calls (8), request_budget_s (90.0), max_revisions (2), reminder_poll_s (30.0)`,
`settings.tz`, `settings.llm_configured`.

DB yolu: `backend/data/assistant.db` (`ensure_data_dir()` klasörü açar).

---

## 4. trace.py (yazıldı)

```python
class ToolInvocation(BaseModel): name: str; args: dict[str, Any]
class JudgeScores(BaseModel): correct: int; complete: int; safe: int
class TraceStep(BaseModel):
    agent: Literal["router","planner","executor","judge","responder","system"]
    decision: str; why: str
    evidence: list[str]; confidence: float | None
    tool: ToolInvocation | None; scores: JudgeScores | None
    verdict: Literal["pass","revise","ask_user"] | None
    latency_ms: int; tokens: int | None; ts: str
def make_step(agent, decision, why, *, evidence=None, confidence=None, tool=None,
              scores=None, verdict=None, latency_ms=0, tokens=None) -> TraceStep
class TraceStore:
    async def init(self) -> None
    async def save(self, request_id: str, step: TraceStep) -> None
    async def history(self, request_id: str, limit: int = 200) -> list[dict]
def make_emitter(send, store, request_id) -> EmitFn   # send frame + persist
EmitFn = Callable[[TraceStep], Awaitable[TraceStep]]
```

`evidence` önekleri: `ref:21`, `url:https://…`, `shot:/shots/r_ab12_2.png`.
SQLite tablosu `traces` trace.py içinde açılır.

---

## 5. llm.py (yazıldı)

```python
class Usage: model, prompt_tokens, completion_tokens, total_tokens, latency_ms, attempts; .tokens
class ToolCall: id, name, args: dict, parse_error: str | None
class ToolMessage: content: str, tool_calls: list[ToolCall]
class LLMClient:
    async def chat_json(system: str, messages: Sequence[dict], temperature: float = 0.0) -> tuple[dict, Usage]
    async def chat_tools(system: str, messages: Sequence[dict], tools: Sequence[dict], temperature: float = 0.2) -> tuple[ToolMessage, Usage]
    configured: bool
def get_llm() -> LLMClient
```
`LLMError` fırlatır. JSON parse hatasında bir kez otomatik retry var.

---

## 6. bridge.py (yazıldı)

```python
class BrowserBridge:
    def __init__(self, send: SendFn, request_id: str | None = None)
    request_id: str | None            # orchestrator her istekte set_request_id ile günceller
    async def call(self, name: str, args: dict, timeout: float | None = None) -> dict
        # {"ok": bool, "data": ..., "error": ...}; timeout -> {"ok": False, "error": "zaman aşımı: ..."}
    def resolve_tool_result(call_id, ok, data, error) -> bool
    async def request_confirm(call_id, summary, tool, args, timeout=None) -> bool   # timeout => False
    def resolve_confirm(call_id, approved: bool) -> bool
    async def close(self) -> None
def new_call_id() -> str  # "c_ab12cd"
```
Aynı `call_id` hem `need_confirm` hem `tool_call` için kullanılabilir; onay
kapısı `new_call_id()` üretir, onaylanırsa tıklama için bridge.call yeni id üretir.

---

## 7. tools/registry.py (yazıldı)

```python
@dataclass class Tool: name, description, parameters, risk: Literal["safe","confirm"], handler
@dataclass class ToolContext: request_id, bridge, tab, emit_trace, extras: dict
def register(tool) ; def get(name) -> Tool | None ; def all_tools() ; def list_for(route) -> list[Tool]
def llm_name(name) / from_llm_name(name)      # browser.click <-> browser__click
def to_llm_schema(tool) / to_llm_schemas(tools)
def inject_why(parameters) / to_llm_schema_with_why(tool)   # executor zorunlu `why` argümanı
def is_risky_label(text) -> bool              # gönder|satın|öde|sipariş|sil|onayla|submit|buy|pay|order|delete|send|confirm
def find_element(snapshot, ref) -> dict | None
def click_needs_confirm(tool, args, snapshot) -> str | None   # onay özeti ya da None
ROUTE_TOOLS: dict[str, tuple[str, ...]]
```
Handler hatada exception fırlatmaz: `{"ok": False, "error": "…"}` döner.

---

## 8. agents/base.py (yazıldı)

```python
class RouterDecision(BaseModel): route: RouteName; confidence: float; why: str
class PlanStep(BaseModel): id: int; action: str; why: str
class Plan(BaseModel): goal: str; steps: list[PlanStep]      # en fazla 5 adım
class ExecutorAction(BaseModel): kind: Literal["tool","finish"]; tool: str | None;
      args: dict; why: str; summary: str; evidence: list[str]
class JudgeVerdict(BaseModel): scores: dict[str,int]; verdict: Literal["pass","revise","ask_user"]; feedback: str
class Citation(BaseModel): title: str; url: str; ref: int | None
class FinalAnswer(BaseModel): answer: str; spoken: str; citations: list[Citation]
      # .spoken_text() markdown/URL temizler ve <=2 cümleye indirir

@dataclass class HistoryEntry: tool, args, ok, data, error, why, rejected; .render() -> str
def build_context_block(*, tab: dict | None, memories: list[dict] | None) -> str
def render_snapshot(snapshot: dict | None, *, max_elements: int = 150) -> str

class BaseAgent:
    name: str            # "router" | "planner" | ...
    prompt_file: str     # "router.md"
    def __init__(self, client: LLMClient, prompts_dir: Path | None = None)
    def system_prompt(self, context_block: str = "") -> str
    async def emit_step(self, emit, *, decision, why, evidence=None, confidence=None,
                        tool=None, scores=None, verdict=None, usage=None, latency_ms=None) -> TraceStep
    async def complete_json(self, messages, *, temperature, context_block="") -> tuple[dict, Usage]
```

### Ajan modüllerinin ZORUNLU imzaları

```python
# agents/router.py
class RouterAgent(BaseAgent):
    name = "router"; prompt_file = "router.md"
    async def route(self, *, user_text: str, tab: dict | None,
                    memories: list[dict] | None, emit: EmitFn) -> RouterDecision

# agents/planner.py
class PlannerAgent(BaseAgent):
    name = "planner"; prompt_file = "planner.md"
    async def plan(self, *, user_text: str, route: str, tab: dict | None,
                   memories: list[dict] | None, emit: EmitFn) -> Plan

# agents/executor.py
class ExecutorAgent(BaseAgent):
    name = "executor"; prompt_file = "executor.md"
    async def step(self, *, goal: str, plan: Plan, route: str, history: list[HistoryEntry],
                   snapshot: dict | None, tools: list[Tool], emit: EmitFn) -> ExecutorAction
    # tool adını registry.get() ile çözer, `why`'ı args'tan ÇIKARIR (handler'a gitmez),
    # bilinmeyen tool adında ExecutorAction(kind="finish", summary="...") benzeri güvenli çıktı
    # ya da hata metni döner. `finish` çağrısı tool_call listesinden bağımsız yakalanır.

# agents/judge.py
class JudgeAgent(BaseAgent):
    name = "judge"; prompt_file = "judge.md"
    async def judge(self, *, goal: str, plan: Plan, history: list[HistoryEntry],
                    snapshot: dict | None, emit: EmitFn) -> JudgeVerdict
    # trace: agent="judge", scores=dolu, verdict=dolu

# agents/responder.py
class ResponderAgent(BaseAgent):
    name = "responder"; prompt_file = "responder.md"
    async def respond(self, *, goal: str, route: str, result_summary: str,
                      evidence: list[str], snapshot: dict | None, history: list[HistoryEntry],
                      emit: EmitFn) -> FinalAnswer
```

Her ajan kendi trace adımını **kendi içinde** `emit_step(...)` ile yayar
(orchestrator ajan adına trace üretmez; sadece sistem/onay adımlarını üretir).
Sıcaklıklar: router 0, planner 0.2, executor 0.2, judge 0, responder 0.4.

---

## 9. WebSocket protokolü (DONMUŞ — ORTAK BAĞLAM §6)

İstemci→sunucu: `hello`, `user_msg`, `tool_result`, `confirm`, `cancel`.
Sunucu→istemci: `trace`, `tool_call`, `need_confirm`, `final`, `notify`, `error`.
Her frame `type` alanı taşır. Bağlantı başına **aynı anda tek istek**.

```jsonc
{ "type": "user_msg", "id": "r_ab12", "text": "…", "source": "text"|"voice", "tab": {...}|null }
{ "type": "tool_call", "call_id": "c_9f", "request_id": "r_ab12", "name": "browser.click", "args": {"ref": 12} }
{ "type": "need_confirm", "call_id": "c_9f", "request_id": "r_ab12", "summary": "…", "tool": "browser.click", "args": {...} }
{ "type": "final", "request_id": "r_ab12", "answer": "markdown", "spoken": "≤2 cümle", "citations": [{"title","url","ref"}] }
{ "type": "notify", "text": "…", "spoken": "…" }
{ "type": "error", "request_id": "r_ab12"|null, "message": "…" }
```
Extension'a **yalnızca** `browser.*` tool_call'ları gider; `desktop.*`, `web.*`,
`memory.*`, `reminders.*` backend'de çalışır.

---

## 10. orchestrator.py — ZORUNLU API

```python
class Orchestrator:
    def __init__(self, send: SendFn, *, store: TraceStore | None = None,
                 bridge: BrowserBridge | None = None, client: LLMClient | None = None) -> None
    async def start(self) -> None          # TraceStore.init + hatırlatma arka plan görevi
    async def handle_frame(self, frame: dict[str, Any]) -> None
        # hello -> log; user_msg -> yeni asyncio.Task (tek istek kuralı); 
        # tool_result/confirm -> bridge.resolve_*; cancel -> task.cancel() + error frame
    async def wait_idle(self, timeout: float | None = None) -> None   # CLI/test için
    async def close(self) -> None
async def serve_connection(websocket) -> None   # WS reader loop: accept, hello bekle, frame oku, hata -> error frame, asla çökme
```

Akış:
1. `user_msg` → Router. `chat` ise doğrudan Responder.
2. Diğerleri: Planner → Executor döngüsü, `registry.list_for(route)` tool'larıyla.
3. Sınır: istek başına en fazla **8 tool çağrısı**, toplam **90 sn**.
4. Onay kapısı **her tool çağrısından önce deterministik**:
   `click_needs_confirm(tool, args, snapshot)` sonuç dönerse `need_confirm` gönder,
   cevabı bekle. Trace: `agent="judge", verdict="ask_user", decision="Kullanıcı onayı istendi"`.
   Red → history'ye `rejected=True` girer, tıklama yapılmaz, executor'a
   "kullanıcı reddetti" bilgisi gider.
5. Executor `finish` → Judge. `revise` ise feedback executor'a döner, **en fazla 2 kez**.
6. Her adımda `emit` → `trace` frame + SQLite. Tool çağrıları için `agent="executor"`,
   `tool={"name","args"}` dolu trace yayılır.
7. `cancel` → çalışan görev iptal, `error` frame.
8. Beklenmeyen hata → `error` frame; bağlantı ayakta kalır.

Hatırlatma görevi: 30 sn'de bir `tools.reminders.due()` varsa çağır; dönen her
kayıt için `notify` gönder; modül yoksa `ImportError`'ı yut ve bilgi logla.

---

## 11. main.py beklentisi

- `FastAPI`, CORS `allow_origins=["*"]`, `GET /health` → `{"ok": true}`.
- `WS /ws` → `orchestrator.serve_connection`; import edilemezse echo fallback.
- `voice.stt.router`, `voice.tts.router`, K4'ün `shots` router'ı `include_router`
  ile eklenir; modül yoksa **log + devam** (uygulama açılmaya devam eder).

---

## 12. Fixture formatı (`shared/fixtures/session_example.jsonl`)

Her satır **tek bir sunucu→istemci olayı** (JSON), kronolojik; senaryo 3 ve 4'ü
kapsar: router/planner/executor trace'leri, gerçek `browser.snapshot` tool_call'u,
`tool_result` beklenmez (sadece sunucu olayları), `need_confirm`, onay sonrası
`final`. Trace adımları §4 şemasına birebir uyar.
