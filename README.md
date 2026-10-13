# Polaris — Sesli, Açıklanabilir Kişisel Asistan

Polaris, **Brave tarayıcının yan panelinde** çalışan genel amaçlı bir kişisel asistandır.

- **Sesli:** Bas-konuş. Ses backend'de yazıya çevrilir (faster-whisper), cevap mp3 olarak okunur (edge-tts).
- **Multi-agent:** Router → Planner → Executor ⇄ Tools → Judge → Responder. Hepsi aynı LLM'i (DeepSeek, OpenAI uyumlu) farklı prompt ve JSON şemasıyla kullanır.
- **Açıklanabilir (XAI):** Her ajan adımı bir `TraceStep` üretir; panelde "Neden?" zaman çizelgesinde karar, gerekçe, kanıt, güven ve gecikmeyle görünür.
- **Computer use:** (1) **Tarayıcı** — DOM snapshot → numaralı öğe listesi → `click(ref)`. (2) **Masaüstü (CUA)** — Docker Linux sandbox → ekran görüntüsü → OCR → aynı numaralı liste.
- **Güvenli:** Gönder / satın al / öde / sil gibi aksiyonlar kullanıcı onayı olmadan çalışmaz. Şifre alanlarına yazılmaz. API anahtarları yalnızca `backend/.env` içindedir.

---

## Mimari özeti

```
 Brave                                          Python (127.0.0.1:8000)
+---------------------------+  WS /ws (JSON)   +-----------------------------------------+
| Yan panel (React/MV3)     |<---------------->| FastAPI main.py                         |
|  - sohbet, Neden? zaman   |  POST /stt (webm)|   Orchestrator                          |
|    çizelgesi, onay kutusu |  POST /tts (mp3) |   Router -> Planner -> Executor <-> Tools|
|  - bas-konuş, TTS oynatma |  GET /shots/*    |                   ^          |          |
+-------------+-------------+                  |                   +-- Judge -+ (revise)|
              | tool_call (browser.*)          |   Responder -> final + spoken + citations|
+-------------v-------------+                  |   Onay kapısı (riskli aksiyon)          |
| background + content      |<-- bridge -------|   TraceStore (SQLite)                   |
|  snapshot/click/type/read |                  +--+---------+----------+---------+-------+
+---------------------------+                     |         |          |         |
                                         web.search  memory.*  reminders.*  desktop.*
                                         (Brave API) (SQLite)  (SQLite)     (CUA Docker + OCR)
```

Ayrıntılar: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Donmuş protokol: [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

---

## Hızlı başlangıç (~15 dk, Windows + PowerShell)

Gereksinimler: Python 3.11–3.13 (3.12 önerilir, 3.14 değil), Node 18+, pnpm, Brave. Docker yalnızca masaüstü (CUA) için.

```powershell
# 1) Kur: .venv + backend paketleri + backend\.env + extension bağımlılıkları
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1          # CUA için: -WithCua

# 2) backend\.env içine DEEPSEEK_API_KEY (ve web.search için BRAVE_API_KEY) yaz

# 3) Backend'i başlat (ayrı bir terminalde açık kalsın)
.\scripts\run-backend.ps1            # demo için: -NoReload

# 4) Extension'ı derle
.\scripts\build-extension.ps1

# 5) Her şey tamam mı?
.\.venv\Scripts\python scripts\doctor.py
```

Doğrulama: <http://127.0.0.1:8000/health> → `{"ok": true, ...}`.
İlk `/stt` çağrısında whisper modeli (`small`, ~460 MB) indirilir; bu bir kez olur.

### Brave'e yükleme

1. `brave://extensions` adresini aç.
2. Sağ üstten **Geliştirici modu**nu aç.
3. **Paketlenmemiş uzantıyı yükle** → `extension\dist` klasörünü seç.
4. Polaris simgesine tıkla (yan panel açılır). Panel **Ayarlar → Backend adresi**: `http://localhost:8000`.

Kod değiştirdikten sonra `build-extension.ps1`'i tekrar çalıştır ve `brave://extensions` içinde uzantıyı yenile.

### Mikrofon izni

Brave içindeki `webkitSpeechRecognition` desteklenmez; bu yüzden ses panelden kaydedilip backend'e (`/stt`) gönderilir.
İlk **Bas-konuş**'ta izin penceresi çıkar → **İzin ver**. Reddettiysen: `brave://settings/content/microphone`
(uzantı için izin ver). Mikrofon çalışmazsa metin kutusu her zaman kullanılabilir.

---

## Ortam değişkenleri (`backend/.env`)

`backend/.env.example` kopyalanır (setup bunu yapar). Gerçek anahtarları **asla** git'e ekleme (`.env` .gitignore'dadır).

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | **Zorunlu.** LLM anahtarı |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI uyumlu başka bir ağ geçidi için değiştir |
| `LLM_MODEL` | `deepseek-chat` | Model adı |
| `BRAVE_API_KEY` | boş | `web.search` için; boşsa araç hata döner |
| `STT_MODEL` | `small` | `small` \| `base` (base ~2× hızlı, Türkçede daha zayıf) |
| `STT_CPU_THREADS` | mantıksal çekirdek/2 | Çekirdek sayısının tamamını verme (yavaşlatır) |
| `TTS_VOICE` | `tr-TR-EmelNeural` | edge-tts sesi |
| `TIMEZONE` | `Europe/Istanbul` | Hatırlatıcılar için |
| `CUA_ENABLED` | `false` | Masaüstü araçlarını açar (Docker gerekir) |
| `DESKTOP_PARSER` | `ocr` | `ocr` \| `omniparser` |
| `ASSISTANT_DB` | `backend/data/assistant.db` | Test için ayrı veritabanı |
| `LLM_TIMEOUT_S` / `TOOL_TIMEOUT_S` / `CONFIRM_TIMEOUT_S` | 30 / 20 / 60 | Zaman aşımları |
| `MAX_TOOL_CALLS` / `REQUEST_BUDGET_S` / `MAX_REVISIONS` | 8 / 90 / 2 | İstek başına bütçe ve revizyon sınırı |
| `REMINDER_POLL_S` | `30` | Hatırlatıcı tarama aralığı |

`.env` değişince backend'i yeniden başlat.

---

## Demo senaryoları

Tam akış ve ters giderse planları: [`demo/script.md`](demo/script.md).

1. "Bu sayfayı özetle" — `browser.read`, sesli cevap.
2. "Brave'de en iyi 3 kablosuz kulaklığı ara, karşılaştır" — `web.search`, kaynaklı tablo.
3. "Bu sitede mekanik klavye ara, ilk 3 fiyatı söyle" — snapshot → type → read, highlight.
4. "Bu formu doldur ve gönder" — göndermeden önce **onay** ister.
5. "Yarın saat 10'da toplantıyı hatırlat" — `reminders.add`, zamanı gelince `notify`.
6. (CUA açıksa) "Masaüstünde metin editörünü aç ve Merhaba yaz" — ekran görüntüleri zaman çizelgesinde.

---

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Panel "Bağlantı yok" | Backend çalışıyor mu? `/health` aç. Ayarlardaki adres `http://localhost:8000` mi? |
| Cevap gelmiyor / LLM hatası | `DEEPSEEK_API_KEY`, `LLM_MODEL`, `DEEPSEEK_BASE_URL` doğru mu? `.env` sonrası backend'i yeniden başlattın mı? (`/health` → `llm_configured`) |
| `web.search` hata | `BRAVE_API_KEY` boş veya 429 (aylık limit). Anahtarı doldur, yeniden başlat |
| Mikrofon çalışmıyor | `brave://settings/content/microphone`; HTTPS/uzantı sayfası izni; alternatif olarak metin yaz |
| `/stt` yavaş | İlk çağrı model indirir. `STT_MODEL=base` veya `STT_CPU_THREADS=8` dene; demoda `-NoReload` kullan |
| `/tts` 503 | edge-tts internet ister; panel tarayıcı sesine düşer |
| `extension/dist` yok / derleme hatası | `.\scripts\build-extension.ps1`; `pnpm` kurulu mu (`npm i -g pnpm`)? |
| Uzantı sayfayı okuyamıyor | `brave://` ve mağaza sayfalarında content script çalışmaz; normal bir `https://` sekmesi aç, sayfayı yenile |
| `desktop.*` araçları yok | `CUA_ENABLED=true`, Docker Desktop açık, `-WithCua` kurulumu; bkz. [`docs/cua-setup.md`](docs/cua-setup.md) |
| Hatırlatıcı saati kayık | `tzdata` kurulu mu, `TIMEZONE` doğru mu? |
| Port 8000 dolu | `doctor.py` hangi durumda olduğunu söyler; eski uvicorn'u kapat |
| Python 3.14 sorunları | `py -3.12 -m venv .venv` ile yeniden kur |

Hâlâ takılıysan: `python scripts\doctor.py` çıktısını ekibe gönder (gizli değer içermez).

---

## Repo yapısı

```
extension/         MV3 + Vite + React + TS (panel, background, content, bridge)
  src/panel/       K1  Yan panel UI, WS istemcisi, ses kaydı, TTS oynatma
  src/content/     K2  snapshot, actions, highlight
  src/bridge/      K2  executeTool (panelin çağırdığı tek fonksiyon)
backend/
  main.py          FastAPI: /health, /ws, /stt, /tts, /shots, /traces
  orchestrator.py  Ajan döngüsü, onay kapısı, hatırlatıcı döngüsü
  agents/ prompts/ Router, Planner, Executor, Judge, Responder + Türkçe promptlar
  llm.py trace.py bridge.py config.py db.py
  tools/           registry + browser, desktop, search, memory, reminders
  cua/             Docker sandbox oturumu, OCR, işaretli ekran görüntüleri
  voice/           stt.py, tts.py
scripts/           setup / run-backend / build-extension / doctor + smoke ve mock araçları
shared/fixtures/   Örnek WS akışı (mock için)
docs/              PROTOCOL.md (donmuş), ARCHITECTURE.md, cua-setup.md, README.md (ses/araç detayı)
demo/script.md     5 dakikalık demo akışı
```

## Ekip ve branch haritası

| Kişi | Branch | Alan |
|---|---|---|
| K1 | `k1-ui` | Yan panel UI, WS istemcisi, ses kaydı, TTS oynatma |
| K2 | `k2-browser` | background, content script, browser bridge |
| K3 | `k3-agents` | Orkestratör, ajanlar, promptlar, LLM, protokol |
| K4 | `k4-cua` | Masaüstü araçları, CUA, OCR |
| K5 | `k5-voice` | STT/TTS, web.search, memory, reminders, belgeler |
| — | `integration` | Birleştirme ve entegrasyon testleri; sonra `main` |

Kural: başkasının dosyasını değiştirme; `docs/PROTOCOL.md` donmuştur, değişiklik için K3'e sor.

## Güvenlik notları

- **Onay kapısı:** `risk: confirm` araçlar ve adı riskli kalıba uyan tıklamalar (`gönder|satın|öde|sipariş|sil|onayla|submit|buy|pay|order|delete|send|confirm`) `need_confirm` ile kullanıcıya sorulur; 60 sn içinde cevap yoksa **reddedilmiş** sayılır.
- Şifre alanlarına hiçbir zaman yazılmaz.
- API anahtarları yalnızca `backend/.env` içindedir; extension anahtar görmez. `.env` ve `backend/data/` git'e girmez.
- Backend `127.0.0.1`'e bağlanır; CORS geniş olduğu için **portu ağa açma**.
- Sayfa içeriği ve araç çıktıları LLM'e **veri** olarak verilir; sayfadaki talimatlar komut sayılmaz.
- LLM'in ham düşünce zinciri gösterilmez; panel yalnızca kısa gerekçe ve kanıt gösterir.
