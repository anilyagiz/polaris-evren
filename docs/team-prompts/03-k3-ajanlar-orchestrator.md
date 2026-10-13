# ROL PROMPTU — Kişi 3: Ajanlar, Orchestrator ve Protokol

> Önce ORTAK BAĞLAM dosyasını verdim. Şimdi senin görevin bu.

Sen bu projede **Kişi 3**'ün eşli programcısısın. Benim alanım asistanın **beyni**: FastAPI uygulaması, WebSocket, DeepSeek istemcisi, 5 ajan (Router, Planner, Executor, Judge, Responder), tool kayıt sistemi, onay kapısı ve XAI trace kaydı. Protokolün sahibi ve entegrasyonun yöneticisi de benim.

## Çalışma şekli
- Önce en fazla 10 maddelik plan ver, onaylayınca kodla.
- Dosyaları **tam haliyle** ver, başında yolunu yaz. Kısaltma yapma.
- Agent framework kullanma (LangChain, LangGraph, CrewAI vb. yok). Sade async Python.
- Sözleşmeyi değiştirme; değişiklik gerekiyorsa öner ama kodu mevcut sözleşmeye göre yaz.

## Sahip olduğum dosyalar
`backend/main.py`, `orchestrator.py`, `bridge.py`, `llm.py`, `trace.py`, `agents/*.py`, `prompts/*.md`, `tools/registry.py`, `tools/browser.py`, `requirements.txt`, `.env.example`, `shared/fixtures/session_example.jsonl`, `scripts/mock_ws.py`, `backend/cli.py`.

## Görevler (sırayla)

### Saat 0–1: İskelet (hemen push'la, herkes bekliyor)
- Repo yapısı ORTAK BAĞLAM §3'teki gibi; boş modüller ve `__init__.py`'ler.
- `main.py`: FastAPI, CORS (`allow_origins=["*"]`), `GET /health`, `WS /ws` (şimdilik echo), `app.include_router` ile `voice.stt.router`, `voice.tts.router` ve K4'ün `/shots` router'ı (dosya yoksa import hatasını yakala ve logla, uygulama yine açılsın).
- `requirements.txt`: fastapi, uvicorn[standard], pydantic, openai, httpx, python-dotenv. Diğerleri kendi satırlarını ekleyecek.
- `.env.example` (ORTAK BAĞLAM §4).
- `tools/registry.py`: `Tool`, `ToolContext`, `register`, `get`, `list_for(route)`, LLM şemasına çeviri (`browser.click` ↔ `browser__click`, çünkü OpenAI uyumlu function adları nokta kabul etmez).
- `shared/fixtures/session_example.jsonl`: senaryo 3 ve 4'ü kapsayan, her satırı bir sunucu→istemci olayı olan gerçekçi akış (trace'ler, bir `tool_call`, bir `need_confirm`, `final`).

### Saat 1–2: LLM istemcisi ve mock sunucu
- `llm.py`:
  - `AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)`, model `LLM_MODEL`, timeout 30 sn.
  - `async chat_json(system, messages, temperature) -> (dict, usage)`: `response_format={"type":"json_object"}`. Parse hatasında bir kez "Sadece geçerli JSON döndür" ekleyip tekrar dene.
  - `async chat_tools(system, messages, tools, temperature) -> (message, usage)`: function calling.
  - Her çağrının süresini ve token sayısını döndür.
- `scripts/mock_ws.py`: LLM'siz sahte sunucu (port 8001). `user_msg` gelince fixture'daki trace'leri gönderir, **gerçek** `browser.snapshot` tool_call'u yollar ve extension'dan dönen sonucu loglar, sonra `final` gönderir. K1 ve K2 bununla LLM olmadan uçtan uca test eder.
- `cli.py`: `python -m backend.cli "soru"` ile extension olmadan orchestrator'ı çalıştırır; `browser.*` tool'ları sahte snapshot döner. Prompt geliştirmeyi hızlandırır.

### Saat 2–4: Ajanlar ve orchestrator
`prompts/*.md` Türkçe olsun; her birinde rol, kurallar, JSON çıktı şeması ve 2 örnek bulunsun. Sistem promptuna her istekte şunları ekle: şimdiki tarih/saat (`TIMEZONE`), aktif sekme bilgisi, `memory.search` ile bulunan en fazla 3 ilgili hafıza kaydı.

| Ajan | Girdi | Çıktı | Ayar |
|---|---|---|---|
| Router | kullanıcı mesajı + sekme | `{route: "chat"\|"web_search"\|"browser_task"\|"desktop_task"\|"personal", confidence, why}` | temp 0. `CUA_ENABLED=false` ise `desktop_task` seçeneğini prompttan çıkar |
| Planner | mesaj + route | `{goal, steps:[{id, action, why}]}` (≤5 adım) | temp 0.2 |
| Executor | plan + geçmiş + son snapshot | function call; her tool şemasına zorunlu `why` (string) argümanı ekle, handler'a göndermeden çıkar. İş bitince `finish {summary, evidence}` çağırır | temp 0.2 |
| Judge | hedef + plan + yapılan adımlar + sonuç | `{scores:{correct, complete, safe}, verdict, feedback}` | temp 0 |
| Responder | hedef + sonuç + kanıt | `{answer, spoken, citations}` — `spoken` ≤2 cümle, markdown ve URL içermez | temp 0.4 |

`orchestrator.py` akışı:
1. `user_msg` → Router. `chat` ise doğrudan Responder.
2. Diğer route'lar: Planner → Executor döngüsü. Executor'a yalnızca route'a uygun tool'ları ver (`registry.list_for(route)`):
   - `browser_task`: `browser.*`, `web.search`
   - `web_search`: `web.search`, `browser.read`, `browser.navigate`
   - `desktop_task`: `desktop.*`
   - `personal`: `memory.*`, `reminders.*`
3. Döngü sınırı: istek başına en fazla 8 tool çağrısı ve toplam 90 sn.
4. **Onay kapısı** (her tool çağrısından önce, deterministik): `tool.risk == "confirm"` ise ya da tıklanan öğenin adı (son snapshot'tan ref ile bul) riskli kalıba uyuyorsa (ORTAK BAĞLAM §7) `need_confirm` gönder ve bekle. Bunu `agent:"judge", verdict:"ask_user"` olarak trace'e yaz. Red → Executor'a "kullanıcı reddetti" mesajı ver ve devam et ya da bitir.
5. Executor `finish` dedikten sonra Judge. `revise` ise feedback'i Executor'a ver ve döngüye dön (en fazla 2 kez). `pass` veya limit dolunca Responder.
6. Her adımda `trace.emit(step)`: WebSocket'e `trace` olayı gönderir ve SQLite `traces` tablosuna yazar.
7. `cancel` gelince çalışan görevi `asyncio.Task.cancel()` ile durdur ve `error` gönder.
8. Beklenmeyen hata → `error` olayı. Bağlantı asla çökmesin.

`bridge.py`: Bağlantı başına `BrowserBridge`. `async call(name, args, timeout=20)` → `call_id` üretir, `tool_call` gönderir, `tool_result` gelene kadar `Future` ile bekler.

`tools/browser.py`: 7 `browser.*` tool'unu registry'ye kaydeder; handler'lar sadece `ctx.bridge.call(...)` çağırır.

Bağlantı başına arka plan görevi: 30 sn'de bir `tools.reminders.due()` (K5) çağır, gelen her kayıt için `notify` gönder. Modül yoksa sessizce atla.

### Saat 5–7: Entegrasyon yöneticisi
- Branch'leri `main`'e merge et, çakışmaları çöz.
- Çalıştırma sırası: backend → extension yükle → panel → senaryo 1, 3, 2, 5, 4, 6.
- Her senaryoda trace'i incele, prompt'ları düzelt. Sık görülen sorunlar: model geçersiz ref kullanıyor (snapshot'ı yenilemesini söyle), JSON dışı çıktı, gereksiz uzun planlar.

## Kabul kriterleri
- [ ] `/health` çalışıyor; diğer modüller eksik olsa bile uygulama açılıyor.
- [ ] `python -m backend.cli "merhaba"` chat route'undan cevap veriyor; `"Brave'de X ara"` web_search akışını trace'leriyle yazdırıyor.
- [ ] Riskli öğeye tıklama her zaman `need_confirm` üretiyor; red sonrası tıklama yapılmıyor.
- [ ] Judge `revise` döngüsü en fazla 2 kez dönüyor.
- [ ] Her trace adımı ORTAK BAĞLAM §6 `TraceStep` şemasına uyuyor ve SQLite'a yazılıyor.
- [ ] Tool çağrısı zaman aşımı ya da extension kopması isteği kilitlemiyor.
