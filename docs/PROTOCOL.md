# ORTAK BAĞLAM — Kişisel Asistan MVP

> Bu dosyayı yapay zekana **ilk mesaj** olarak ver. Hemen ardından kendi rol promptunu (01–05) ver.
> Bu dosyadaki sözleşmeler **donmuştur**. Değiştirmek gerekiyorsa önce Kişi 3'e (protokol sahibi) sor.

## 1. Proje

5 kişilik ekip, 10 saatte MVP. Brave tarayıcıda **yan panelde** çalışan, sesli, açıklanabilir ve bilgisayar kullanabilen genel amaçlı kişisel asistan.

- **Ses:** Bas-konuş. Ses backend'de yazıya çevrilir (Brave tarayıcı içi `webkitSpeechRecognition`'ı desteklemez). Cevap mp3 olarak sesli okunur.
- **Multi-agent:** Router → Planner → Executor ⇄ Tools → Judge → Responder. Tüm ajanlar aynı LLM'i kullanır, farkları prompt ve JSON şemasıdır.
- **Explainable AI:** Her ajan adımı yapılandırılmış bir `TraceStep` üretir ve paneldeki "Neden?" zaman çizelgesinde görünür.
- **Computer use:** (1) Tarayıcı: DOM snapshot → numaralı öğe listesi → `click(ref)`. (2) Masaüstü: CUA Docker sandbox → screenshot → OCR/OmniParser → numaralı öğe listesi → `click(ref)`. LLM her iki durumda da aynı formatı görür.
- **LLM:** DeepSeek, OpenAI uyumlu API (`openai` Python SDK + `base_url`). Model adı `.env` içindeki `LLM_MODEL`. Modelin görsel girdi almadığını varsay.
- **Güvenlik:** Gönder/satın al/öde/sil/mesaj gönder gibi aksiyonlar kullanıcı onayı olmadan çalışmaz. Şifre alanlarına asla yazılmaz. API key'ler yalnızca backend `.env` dosyasında.

## 2. Teknoloji

| Katman | Seçim |
|---|---|
| Extension | Manifest V3, Vite + React + TypeScript (strict), `@crxjs/vite-plugin` |
| Backend | Python 3.11, FastAPI, uvicorn, pydantic v2, `openai`, `httpx` |
| Veri | Tek SQLite dosyası: `backend/data/assistant.db`. Her modül kendi tablosunu `CREATE TABLE IF NOT EXISTS` ile açar |
| STT / TTS | `faster-whisper` (small, int8, CPU) / `edge-tts` (tr-TR-EmelNeural) |
| Arama | Brave Search API |
| Masaüstü | CUA (`trycua/cua`) Docker Linux sandbox. Not: Windows 10 Home'da Windows Sandbox yok, Docker Desktop + WSL2 kullan |

## 3. Repo yapısı ve sahiplik

```
assistant/
├─ extension/                      
│  ├─ manifest.json                K1
│  ├─ vite.config.ts, package.json K1
│  ├─ src/panel/**                 K1  (UI, WebSocket istemcisi, ses kaydı, TTS oynatma)
│  ├─ src/background.ts            K2
│  ├─ src/content/**               K2  (snapshot, actions, highlight)
│  └─ src/bridge/executeTool.ts    K2  (panelin çağırdığı tek fonksiyon)
├─ backend/
│  ├─ main.py                      K3  (FastAPI app, /ws, /health, CORS, router include)
│  ├─ orchestrator.py, bridge.py   K3
│  ├─ llm.py, trace.py             K3
│  ├─ agents/*.py, prompts/*.md    K3
│  ├─ tools/registry.py            K3
│  ├─ tools/browser.py             K3  (bridge üzerinden extension'a giden ince sarmal)
│  ├─ tools/desktop.py, cua/**     K4
│  ├─ tools/search.py              K5
│  ├─ tools/memory.py              K5
│  ├─ tools/reminders.py           K5
│  ├─ voice/stt.py, voice/tts.py   K5  (APIRouter; K3 main.py'de include eder)
│  ├─ requirements.txt             K3 oluşturur, herkes kendi satırını ekler
│  └─ .env.example                 K3
├─ shared/fixtures/session_example.jsonl   K3 (örnek WS akışı, mock için)
├─ scripts/mock_ws.py              K3
└─ docs/README.md, demo/script.md  K5
```

Kurallar:
- Başkasının dosyasını değiştirme. İhtiyacın varsa sahibine mesaj at.
- Kod ve tanımlayıcılar İngilizce, kullanıcıya görünen metinler Türkçe.
- Python: type hint, async, `logging.getLogger(__name__)`. TS: strict, `any` yok.
- Git: her kişi kendi branch'inde (`k1-ui`, `k2-browser`, `k3-agents`, `k4-cua`, `k5-voice`). 5. saatte `main`'e merge.

## 4. Ortam değişkenleri (`backend/.env`)

```
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=            # DeepSeek dokümanından doğru model adını yaz
BRAVE_API_KEY=
STT_MODEL=small
TTS_VOICE=tr-TR-EmelNeural
TIMEZONE=Europe/Istanbul
CUA_ENABLED=false
DESKTOP_PARSER=ocr    # ocr | omniparser
```

Backend portu `8765`. Extension backend adresini ayarlardan okur, varsayılan `http://localhost:8765`.

## 5. HTTP uç noktaları

| Metot | Yol | Girdi | Çıktı | Sahip |
|---|---|---|---|---|
| GET | `/health` | — | `{"ok": true}` | K3 |
| WS | `/ws` | aşağıdaki protokol | | K3 |
| POST | `/stt` | multipart, alan adı `audio` (audio/webm) | `{"text", "language", "duration_s"}` | K5 |
| POST | `/tts` | JSON `{"text"}` | `audio/mpeg` | K5 |
| GET | `/settings/llm` | — | `{presets[], provider, base_url, model, fallback_model, thinking: "on"\|"off", api_key_set, api_key_hint, brave_key_set, llm_configured}` (anahtar asla dönmez) | K3 |
| PUT | `/settings/llm` | JSON, hepsi isteğe bağlı: `{base_url, model, fallback_model ("" / null siler), thinking, api_key (boşsa değişmez), brave_api_key ("" değişmez, null siler)}` | GET ile aynı; `backend/.env` atomik yazılır, yeniden başlatmadan uygulanır. Geçersiz girdi → 422 (Türkçe `detail`) | K3 |
| POST | `/settings/llm/test` | JSON `{base_url?, model?, api_key?}` (eksikler mevcut ayardan) | `{ok, latency_ms, model, message}` (tek küçük sohbet çağrısı, 20 sn) | K3 |
| GET | `/settings/llm/models` | `?base_url=&preset=` | `{models: [{id, label, note}], source: "api"\|"preset"}` | K3 |
| GET | `/shots/{name}` | — | `image/png` (masaüstü ekran görüntüsü, işaretli) | K4 |

CORS: yalnızca `chrome-extension://<id>` ve localhost origin'lerine izin verilir. `/settings/*` ayrıca loopback olmayan istemcileri ve izin dışı `Origin` başlığını 403 ile reddeder; gövde ≤ 4 KB.

## 6. WebSocket protokolü (DONMUŞ)

`ws://localhost:8765/ws`. Her frame bir JSON nesnesi ve mutlaka `type` alanı içerir. Bağlantı başına aynı anda tek istek işlenir.

### İstemci → Sunucu
```jsonc
{ "type": "hello", "client": "extension" | "desktop", "version": "0.1" }
// "desktop" = Polaris masaüstü penceresi (/app/): browser.* araçları kapalı,
// browser_task rotası sohbete düşürülür.
{ "type": "user_msg", "id": "r_ab12", "text": "…", "source": "text" | "voice",
  "tab": { "id": 123, "url": "…", "title": "…" } | null }
{ "type": "tool_result", "call_id": "c_9f", "ok": true, "data": { … } | null, "error": "…" | null }
{ "type": "confirm", "call_id": "c_9f", "approved": true }
{ "type": "cancel", "id": "r_ab12" }
```

### Sunucu → İstemci
```jsonc
{ "type": "trace", "request_id": "r_ab12", "step": TraceStep }
{ "type": "tool_call", "call_id": "c_9f", "request_id": "r_ab12", "name": "browser.click", "args": { "ref": 12 } }
{ "type": "need_confirm", "call_id": "c_9f", "request_id": "r_ab12", "summary": "“Siparişi tamamla” düğmesine basılsın mı?", "tool": "browser.click", "args": { "ref": 52 } }
{ "type": "final", "request_id": "r_ab12", "answer": "markdown", "spoken": "düz metin, ≤2 cümle", "citations": [ { "title": "…", "url": "…" , "ref": 21 } ] }
{ "type": "notify", "text": "Hatırlatma: toplantı 10:00", "spoken": "…" }
{ "type": "error", "request_id": "r_ab12" | null, "message": "…" }
```

- Extension'a yalnızca `browser.*` isimli `tool_call`'lar gelir. `desktop.*`, `web.*`, `memory.*`, `reminders.*` backend'de çalışır.
- `tool_result` 20 saniye içinde gelmezse backend çağrıyı hata sayar.
- `need_confirm` cevabı 60 saniye içinde gelmezse reddedilmiş sayılır.

### TraceStep
```jsonc
{
  "agent": "router" | "planner" | "executor" | "judge" | "responder" | "system",
  "decision": "kısa karar cümlesi",
  "why": "kullanıcıya gösterilecek gerekçe (ham düşünce zinciri değil)",
  "evidence": ["ref:21", "url:https://…", "shot:/shots/r_ab12_2.png", "serbest metin"],
  "confidence": 0.0-1.0 | null,
  "tool": { "name": "browser.click", "args": { "ref": 7 } } | null,
  "scores": { "correct": 1-5, "complete": 1-5, "safe": 1-5 } | null,   // sadece judge
  "verdict": "pass" | "revise" | "ask_user" | null,                  // sadece judge
  "latency_ms": 840,
  "tokens": 512 | null,
  "ts": "2026-10-13T14:02:11+03:00"
}
```
`evidence` önekleri: `ref:` sayfadaki öğe (panel tıklayınca highlight eder), `url:` link, `shot:` backend'den görsel yolu.

## 7. Tool sözleşmesi

Backend'deki her tool `tools/registry.py` ile kaydedilir:

```python
@dataclass
class Tool:
    name: str                 # "browser.click" — LLM'e giderken "browser__click" olur (K3 çevirir)
    description: str          # Türkçe, LLM için
    parameters: dict          # JSON Schema (object)
    risk: Literal["safe", "confirm"]
    handler: Callable[[dict, "ToolContext"], Awaitable[dict]]

@dataclass
class ToolContext:
    request_id: str
    bridge: "BrowserBridge"   # browser.* için
    tab: dict | None
    emit_trace: Callable[[dict], Awaitable[None]]

registry.register(tool: Tool) -> None
```
Handler hata durumunda exception fırlatmak yerine `{"ok": false, "error": "…"}` döner.

### Tool listesi

| Ad | Argümanlar | Dönüş | Risk | Sahip |
|---|---|---|---|---|
| `browser.snapshot` | `{}` | `Snapshot` | safe | K2 (+K3 sarmal) |
| `browser.click` | `{ref}` | `{ok}` | safe* | K2 |
| `browser.type` | `{ref, text, submit?: bool}` | `{ok}` | submit=true ise confirm | K2 |
| `browser.scroll` | `{direction: "up"\|"down"}` | `{ok}` | safe | K2 |
| `browser.navigate` | `{url}` | `{ok, url, title}` | safe | K2 |
| `browser.open` | `{url, new_tab?: bool = true}` | `{ok, url, title, new_tab}` | safe | K2 |
| `browser.read` | `{ref?}` | `{text}` (≤4000 karakter) | safe | K2 |
| `browser.highlight` | `{ref}` | `{ok}` | safe | K2 |
| `desktop.snapshot` | `{}` | `Snapshot` | safe | K4 |
| `desktop.click` | `{ref}` | `{ok}` | safe* | K4 |
| `desktop.type` | `{text}` | `{ok}` | safe | K4 |
| `desktop.key` | `{combo}` örn. `"ctrl+l"` | `{ok}` | safe | K4 |
| `desktop.scroll` | `{direction}` | `{ok}` | safe | K4 |
| `web.search` | `{query, count?: 5}` | `{results: [{title, url, snippet}]}` | safe | K5 |
| `memory.save` | `{text, tags?: [str]}` | `{ok, id}` | safe | K5 |
| `memory.search` | `{query}` | `{items: [{id, text, created_at}]}` | safe | K5 |
| `reminders.add` | `{text, due_iso}` | `{ok, id}` | safe | K5 |
| `reminders.list` | `{}` | `{items: [{id, text, due_iso}]}` | safe | K5 |

\* Click'ler varsayılan olarak güvenli. Ama tıklanan öğenin adı riskli bir kalıba uyuyorsa (`gönder|satın|öde|sipariş|sil|onayla|submit|buy|pay|order|delete|send|confirm`) orchestrator'daki onay kapısı (K3) `need_confirm` gönderir.

**Görev sekmesi (task tab).** Panel her `tool_call`'ı isteğin sekmesiyle (`tabId`) ve `request_id` ile köprüye verir. `browser.open` yeni bir sekme açar (yalnızca `http/https`; `navigate` ile aynı izin listesi), sekmeyi ve penceresini öne getirir, yüklenmesini bekler ve **o isteğin görev sekmesi** yapar: aynı `request_id`'nin sonraki bütün `browser.*` çağrıları bu sekmeye gider (`navigate` de görev sekmesini günceller). Aktif sekme kısıtlı/boş bir sayfaysa (`brave://newtab`, `chrome://…`, boş yeni sekme) `browser.navigate` o sekmeyi yönlendirir; bu mümkün değilse hata vermek yerine yeni sekmede açar. `new_tab: false` ile `browser.open`, `navigate` gibi davranır.

**Görünür el (visible hand).** `click`/`type`/`scroll`/`highlight`/`read(ref)` sayfada çalışırken içerik betiği kapalı bir Shadow DOM katmanında (`pointer-events: none`, en üst `z-index`, sayfa düzenini etkilemez) altın renkli bir Polaris imleci gösterir: imleç hedef öğenin merkezine yumuşakça kayar (~300–450 ms), tıklamada dalga efekti yapar, `type`'ta metni harf harf yazar (en fazla ~1 s, sonra son değer React-güvenli biçimde atanır), öğeyi kısaca çerçeveler ve Türkçe kısa bir etiket gösterir ("Arama kutusuna yazıyorum"). `prefers-reduced-motion` açıksa animasyon yoktur. Araç argümanları değişmedi; isteğe bağlı `label` (metin) gelirse etiket olarak kullanılır, gelmezse etiket öğenin adından türetilir. Eylem başına ek gecikme ≤ ~1,5 s.

### Snapshot formatı (browser ve desktop aynı)
```jsonc
{
  "source": "browser" | "desktop",
  "url": "…", "title": "…",                       // desktop'ta null olabilir
  "elements": [ { "ref": 12, "role": "button", "name": "Sepete ekle", "value": null, "bbox": [x, y, w, h] } ],
  "text_excerpt": "sayfanın ana metninden ilk ~1500 karakter",
  "shot": "/shots/r_ab12_2.png" | null           // sadece desktop
}
```
En fazla 150 öğe, `name` en fazla 80 karakter. LLM'e şu biçimde verilir: `[12] button "Sepete ekle"`.

## 8. Demo senaryoları (kabul testleri)

1. Ses: "Bu sayfayı özetle" → `browser.read` + kısa özet + sesli cevap.
2. "Brave'de en iyi 3 kablosuz kulaklığı ara, karşılaştır" → `web.search` + kaynaklı tablo.
3. "Bu sitede mekanik klavye ara, ilk 3 fiyatı söyle" → snapshot, type, read; zaman çizelgesinde highlight.
4. "Bu formu doldur ve gönder" → alanları doldurur, **göndermeden önce onay ister**.
5. "Yarın saat 10'da toplantıyı hatırlat" → `reminders.add`; zamanı gelince `notify`.
6. (CUA açıksa) "Masaüstünde metin editörünü aç ve 'Merhaba' yaz" → desktop snapshot ekran görüntüleriyle zaman çizelgesinde.

## 9. Zaman çizelgesi

| Saat | İş |
|---|---|
| 0–1 | K3 repo iskeleti + `.env.example` + fixture'ı push'lar. K1 extension iskeletini push'lar. Herkes bu dosyayı okur |
| 1–5 | Herkes kendi alanında, mock'larla çalışır. **3. saatte 15 dk senkron** |
| 5–7 | Merge + entegrasyon: metin → cevap, browser task, ses |
| 6 | CUA kararı: çalışmıyorsa K4 entegrasyona katılır, CUA demo videoya alınır |
| 7–8.5 | Judge/revise, onay kapısı, highlight, hata durumları |
| 8.5–9.5 | Demo senaryoları, prompt ayarı, hız |
| 9.5–10 | Tampon, README, yedek demo videosu |
