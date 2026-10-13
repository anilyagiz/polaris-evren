# Polaris — Mimari

Donmuş sözleşme: [`PROTOCOL.md`](PROTOCOL.md). Bu belge onu özetler ve uygulamanın nasıl çalıştığını anlatır.

## 1. Bileşenler

| Bileşen | Yer | Görev |
|---|---|---|
| Panel | `extension/src/panel` | Sohbet, "Neden?" zaman çizelgesi, onay kutusu, bas-konuş, TTS oynatma, `/ws` istemcisi |
| Background / content | `extension/src/background.ts`, `src/content/**` | DOM snapshot, click/type/scroll/read/highlight, sekme yönetimi |
| Bridge (extension) | `extension/src/bridge/executeTool.ts` | Panelin `browser.*` çağrısını çalıştıran tek fonksiyon |
| FastAPI | `backend/main.py` | `/health`, `/ws`, `/stt`, `/tts`, `/shots/{name}`, `/traces/{request_id}`, CORS |
| Orkestratör | `backend/orchestrator.py` | Bağlantı başına tek istek; ajan döngüsü, araç çalıştırma, onay kapısı, bütçe, hatırlatıcı döngüsü |
| Ajanlar | `backend/agents/*.py`, `backend/prompts/*.md` | Router, Planner, Executor, Judge, Responder |
| LLM | `backend/llm.py` | `openai` SDK + `base_url` (DeepSeek); zaman aşımı, JSON çıktı |
| Trace | `backend/trace.py` | `TraceStep` üretimi/saklaması (SQLite), `/traces` ile tekrar oynatma |
| Bridge (backend) | `backend/bridge.py` | `browser.*` çağrılarını WS üzerinden extension'a gönderir, `tool_result` bekler (20 sn) |
| Araçlar | `backend/tools/` | `registry.py` + `browser`, `desktop`, `search`, `memory`, `reminders` |
| CUA | `backend/cua/` | Docker sandbox oturumu, ekran görüntüsü, OCR ile öğe çıkarma, işaretli PNG |
| Ses | `backend/voice/` | `stt.py` (faster-whisper small, int8, CPU), `tts.py` (edge-tts, önbellekli) |
| Veri | `backend/data/assistant.db` | Tek SQLite: memories, reminders, trace |

## 2. WebSocket akışı

`ws://localhost:8000/ws`; her frame JSON ve `type` içerir.

```
Panel                         Backend                               Extension (browser.*)
  | hello ------------------->|
  | user_msg {id,text,tab} -->| Router -> Planner -> Executor
  |<-- trace (router) --------|
  |<-- trace (planner) -------|
  |                           |-- araç gerekli:
  |<-- tool_call browser.* ---|   (panel, extension'a iletir)
  | tool_result ------------->|   (20 sn içinde; yoksa hata)
  |<-- need_confirm ----------|   riskli ise
  | confirm {approved} ------>|   (60 sn içinde; yoksa red)
  |<-- trace (executor) ------|
  |<-- trace (judge) ---------|   verdict: pass | revise | ask_user
  |<-- final {answer,spoken,citations}
  |<-- notify (hatırlatıcı) --|   zamanı gelince, istekten bağımsız
  | cancel {id} ------------->|   isteği iptal eder
```

Sunucudan `error {request_id|null, message}` her zaman anlaşılır Türkçe metin taşır. Extension'a yalnızca `browser.*` çağrıları gider; `desktop.*`, `web.*`, `memory.*`, `reminders.*` backend'de çalışır.

## 3. Ajanlar

Hepsi aynı LLM; fark prompt (`backend/prompts/*.md`) ve JSON şemasıdır.

| Ajan | Girdi | Çıktı |
|---|---|---|
| **Router** | Kullanıcı metni, sekme, hafıza | Niyet sınıfı (sohbet / araç gerekir / belirsiz) |
| **Planner** | Niyet, araç listesi | Kısa adım planı |
| **Executor** | Plan + önceki sonuçlar | Sıradaki araç çağrısı veya bitti kararı |
| **Judge** | Hedef + sonuçlar | `scores {correct, complete, safe}` (1-5) + `verdict` |
| **Responder** | Tüm kanıt | `answer` (markdown), `spoken` (≤2 cümle), `citations[]` |

Döngü: Executor araçları çağırır → Judge değerlendirir → `revise` ise Executor'a geri döner (en çok `MAX_REVISIONS`=2) → `pass` ise Responder → `ask_user` ise kullanıcıya soru sorulur. İstek başına sınırlar: `MAX_TOOL_CALLS`=8, `REQUEST_BUDGET_S`=90, LLM 30 sn, araç 20 sn. Bütçe aşılırsa eldeki kanıtla dürüst bir cevap verilir.

## 4. XAI izi (TraceStep)

Her ajan adımı bir `trace` frame'idir ve SQLite'a yazılır (`GET /traces/{request_id}` ile tekrar oynatılır):

| Alan | Anlam |
|---|---|
| `agent` | router, planner, executor, judge, responder, system |
| `decision` | Kısa karar cümlesi |
| `why` | Kullanıcıya gösterilecek gerekçe (ham düşünce zinciri **değil**) |
| `evidence` | `ref:21` (sayfadaki öğe, tıklayınca highlight), `url:…`, `shot:/shots/…png`, serbest metin |
| `confidence` | 0–1 veya null |
| `tool` | Çağrılan araç ve argümanları |
| `scores`, `verdict` | Yalnızca judge |
| `latency_ms`, `tokens`, `ts` | Ölçümler |

Panel bunları kronolojik "Neden?" çizelgesinde gösterir; `ref:` kanıtına tıklamak ilgili öğeyi sayfada vurgular (`browser.highlight`), `shot:` masaüstü ekran görüntüsünü açar.

## 5. Risk kapısı

1. Her aracın `risk` değeri vardır: `safe` veya `confirm` (örn. `browser.type` ile `submit=true`).
2. `safe*` tıklamalarda orchestrator tıklanan öğenin adına bakar. Ad şu kalıba uyarsa onay istenir: `gönder|satın|öde|sipariş|sil|onayla|submit|buy|pay|order|delete|send|confirm`.
3. Backend `need_confirm {call_id, summary, tool, args}` gönderir; panel onay kutusu açar; kullanıcı `confirm {approved}` döner.
4. Onay yoksa, red gelirse veya 60 sn dolarsa araç **çalıştırılmaz**; Executor bunu sonuç olarak görür.
5. Şifre alanlarına yazma araç seviyesinde engellenir.

## 6. Araç tablosu

| Ad | Argümanlar | Dönüş | Risk | Çalıştığı yer |
|---|---|---|---|---|
| `browser.snapshot` | — | Snapshot (≤150 öğe) | safe | Extension |
| `browser.click` | `ref` | `{ok}` | safe* | Extension |
| `browser.type` | `ref, text, submit?` | `{ok}` | submit ise confirm | Extension |
| `browser.scroll` | `direction` | `{ok}` | safe | Extension |
| `browser.navigate` | `url` | `{ok,url,title}` | safe | Extension |
| `browser.read` | `ref?` | `{text}` (≤4000) | safe | Extension |
| `browser.highlight` | `ref` | `{ok}` | safe | Extension |
| `desktop.snapshot` | — | Snapshot + `shot` | safe | Backend (CUA) |
| `desktop.click` | `ref` | `{ok}` | safe* | Backend (CUA) |
| `desktop.type` | `text` | `{ok}` | safe | Backend (CUA) |
| `desktop.key` | `combo` | `{ok}` | safe | Backend (CUA) |
| `desktop.scroll` | `direction` | `{ok}` | safe | Backend (CUA) |
| `web.search` | `query, count?` | `{results[]}` | safe | Backend (Brave API) |
| `memory.save` / `memory.search` | `text,tags?` / `query` | `{ok,id}` / `{items[]}` | safe | Backend (SQLite) |
| `reminders.add` / `reminders.list` | `text,due_iso` / — | `{ok,id}` / `{items[]}` | safe | Backend (SQLite) |

Araç adları LLM'e `browser__click` biçiminde gider. Handler hata durumunda exception yerine `{"ok": false, "error": "…"}` döner. `desktop.*` yalnızca `CUA_ENABLED=true` iken kayıtlıdır.

Snapshot (browser ve desktop aynı): `{source, url, title, elements:[{ref,role,name,value,bbox}], text_excerpt, shot}`. LLM'e `[12] button "Sepete ekle"` biçiminde verilir.

## 7. Ses hattı

Bas-konuş → panel `audio/webm` kaydeder → `POST /stt` → metin `user_msg` (`source: "voice"`) olarak gider → `final.spoken` → `POST /tts` → mp3 oynatılır. `/tts` başarısız olursa panel tarayıcı sesine düşer. Hedef gecikmeler: STT warm < 3 sn, TTS ilk < 1,5 sn, önbellekli < 0,2 sn.

## 8. Hatırlatıcılar

`reminders.add` SQLite'a UTC olarak yazar. Orkestratör her `REMINDER_POLL_S` (30) sn'de vadesi gelenleri tarar ve açık bağlantıya `notify {text, spoken}` gönderir.
