# Kişi 5 → Kişi 3 Devir Notları

Bu dosya, K5'in çalışırken **K3 sahipli** dosyalara dokunduğu yerleri ve
merge sırasında yapılması gerekenleri listeler. ORTAK BAĞLAM §3 kuralı gereği
K3'ün dosyaları üzerinde kalıcı değişiklik iddiası yoktur.

---

## 1. K3 sahipli dosyalarda yapılan geçici dokunuşlar

| Dosya | Durum | Merge'de ne olmalı |
|---|---|---|
| `backend/main.py` | **Tamamen K5 yazdı.** K5 modülleri tek başına test edilebilsin diye `/health` + `/stt` + `/tts` + CORS içeren minimal bir FastAPI uygulaması. `/ws`, orchestrator, bridge, `/shots` yok. | K3'ün asıl `main.py`'si bunun **üzerine** yazılmalı. Aşağıdaki iki `include_router` satırı korunmalı. |
| `backend/tools/registry.py` | K5'in **geçici taslağı** (rol promptu izin veriyor). `Tool`, `ToolContext`, `register`, ayrıca testler için `get()` ve `all_tools()` yardımcıları. | K3'ün gerçek registry'si bunun yerine geçmeli. `get`/`all_tools` yoksa `scripts/test_tools.py` import satırı güncellenmeli. |
| `backend/.env.example` | K5 oluşturdu ve **LLM değerlerini bu makinenin ortamına göre doldurdu**. | Aşağıya bakın (madde 2). |
| `backend/requirements.txt` | K3 oluşturur, herkes kendi satırını ekler kuralı gereği **K5 satırları eklendi** (faster-whisper, av, edge-tts, httpx, tzdata + test/router için fastapi/uvicorn/python-multipart). | K3 kendi satırlarını (openai, pydantic, websockets…) eklesin; K5 satırları olduğu gibi kalsın. |
| `.gitignore` | K5 oluşturdu (`.venv/`, `.env`, `backend/data/`, `node_modules/`, `dist/`). | K3 doğrulasın; `.env` ve `backend/data/` **kesinlikle** ignore'da kalmalı. |

## 2. `.env.example` sapması — K3 onayı gerekiyor

ORTAK BAĞLAM §4:

```
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=            # DeepSeek dokümanından doğru model adını yaz
```

Bu makinede kullanılan gerçek değerler (çalışıyor, doğrulandı):

```
DEEPSEEK_BASE_URL=https://evren-llmapi.ssyz.org.tr/v1
LLM_MODEL=deepseek-v4.1-flash
```

Bu ortam EVREN adlı OpenAI uyumlu bir ağ geçidi kullanıyor. `.env` dosyasının
kendisi git'te değil (`.gitignore`), dolayısıyla anahtar sızmıyor. **Karar K3'ün:**
ya `.env.example`'daki bu iki satır onaylanır, ya ORTAK BAĞLAM'daki değerler geri
yazılır ve gerçek değerler yalnızca yerel `.env`'de tutulur.

## 3. K3'ün `main.py`'sinde olması gerekenler (K5 tarafından beklenen)

```python
# 1) .env'i import sırasının EN BAŞINDA yükle.
#    K5 modülleri kendi load_env()'ini çağırır ama K3'ün llm.py'si
#    os.environ'u doğrudan okursa sıralama yüzünden anahtarı göremez.
from backend.db import load_env
load_env()

# 2) K5 tool'larını import et — kayıt import sırasında olur (decorator yok).
from backend.tools import memory as _memory      # noqa: F401
from backend.tools import reminders as _reminders  # noqa: F401
from backend.tools import search as _search      # noqa: F401

# 3) K5 router'larını ekle.
from backend.voice.stt import router as stt_router
from backend.voice.tts import router as tts_router
app.include_router(stt_router)
app.include_router(tts_router)
```

`backend/tools/__init__.py` **boştur** — hiçbir modülü otomatik import etmez,
registry çift kayıt hatası almaz.

## 4. K5'in K3'e sağladığı arayüzler

| Arayüz | İmza | Not |
|---|---|---|
| `backend.voice.stt.router` | `APIRouter` | `POST /stt`, multipart alan adı `audio` |
| `backend.voice.tts.router` | `APIRouter` | `POST /tts`, JSON `{"text"}` → `audio/mpeg` |
| `backend.tools.memory.relevant` | `async def relevant(query: str, k: int = 3) -> list[str]` | Planner/Responder bağlamı için |
| `backend.tools.reminders.due` | `async def due() -> list[dict]` | 30 sn'de bir çağrılacak; döndürdüğü kayıtları `notified=1` işaretler, **aynı kayıt ikinci kez dönmez** |
| `backend.db.load_env` | `() -> None` | `.env`'i os.environ'a bir kez yükler, mevcut değişkenleri ezmez |
| `backend.db.session` | context manager | commit/rollback + close otomatik |
| `backend.db.db_path` | `() -> Path` | `ASSISTANT_DB` ile yönlendirilebilir |

`due()` ve `list` çıktısındaki `due_iso` **yerel saat** (`+03:00`) biçimindedir;
DB'de UTC saklanır. Aynı anı gösterir, `datetime.fromisoformat` ile okunabilir.

## 5. `/v1/models` 404 gözlemi

Uvicorn çalışırken bir istemci düzenli olarak `GET /v1/models` denedi ve 404 aldı.
Sözleşmede `/v1` proxy'si yoktur: LLM çağrıları backend içinde (K3 `llm.py`)
yapılır, panel backend'e yalnızca `/ws` üzerinden bağlanır. Bunu bir istemcinin
(K1 paneli veya bir ayar) backend'i OpenAI uyumlu bir servis sanması olarak
değerlendiriyoruz — K3 kontrol etsin.

## 6. K5 tarafında bilinen varsayımlar

- `Tool` / `ToolContext` alan adları ORTAK BAĞLAM §7 ile birebir aynı varsayıldı.
- Tool handler'ları **exception fırlatmaz**, `{"ok": false, "error": …}` döner.
- Başarılı dönüşler `{"ok": true, ...}` ile sözleşmedeki alanları içerir
  (örn. `{"ok": true, "results": [...]}`) — sözleşmenin üst kümesi.
- Tool adları LLM'e `browser__click` biçiminde gidecekse çevrimi **K3** yapar.

## 7. K5 tool katmanı birleştirildi — K3'ün bilmesi gereken farklar

K5'in ilk sürümü ile `k5-voice` branch'indeki bağımsız sürüm birleştirildi.
Dış sözleşme (tool adları, argümanlar, `risk`) **değişmedi**, ama iç detaylar
netleşti:

| Konu | Yeni davranış | K3 ne yapmalı |
|---|---|---|
| `id` tipleri | `memory.*` ve `reminders.*` artık **tamsayı** id döndürür (`INTEGER PRIMARY KEY`) | id'yi opak kabul et; string varsayımı yapma |
| `reminders.list` | Sadece **henüz bildirilmemiş** kayıtları döndürür | Zamanı geçmiş kayıt listede görünmez, panik yapma |
| `memory.relevant` | **Asla exception fırlatmaz**, hata durumunda `[]` döner | Sarmalamana gerek yok |
| DB erişimi | Tüm DB çağrıları `asyncio.to_thread` içinde; event loop bloklanmaz | — |
| `due()` | `BEGIN IMMEDIATE` ile atomik; `--workers > 1` altında da çift bildirim olmaz | 30 sn döngü güvenli |
| Eski veritabanı | `memories`/`reminders` eski şemadaysa **ilk kullanımda otomatik göç eder** (`id TEXT` → `INTEGER`, `text_norm` eklenir) | Bir defalık yavaşlık normal, log'da `rebuilding the table` görünür |
| `web.search` `count` | 1–20 arası kabul eder (Brave limiti), fazlası kırpılır | — |
| Brave hataları | 429, 401/403 ve **422 `SUBSCRIPTION_TOKEN_INVALID`** ayrı ayrı anlaşılır mesaja çevrilir | — |
| TTS motoru | **Sadece edge-tts.** `gTTS` denendi ama `click<8.2` isterken `huggingface-hub` `click>=8.4.2` istiyor; ikisi aynı ortamda çalışmıyor | `gTTS` eklemeye çalışma |

`docs/README.md` §7'de bu maddelerin kullanıcıya dönük karşılıkları var.

