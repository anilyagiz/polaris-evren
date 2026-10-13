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
 Brave                                          Python (127.0.0.1:8765)
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

Mimari ayrıntılar: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Donmuş protokol: [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

---

## 5 dakikada başla

**Gereksinimler** (bir kez kurulur):

| Yazılım | Sürüm | Not |
|---|---|---|
| Windows 10/11 | — | Betikler PowerShell 5.1 ile çalışır |
| [Python](https://www.python.org/downloads/) | **3.12** (3.11–3.13) | Kurarken "Add python.exe to PATH". 3.14 **kullanmayın** (faster-whisper) |
| [Node.js](https://nodejs.org) | 18+ (22 önerilir) | npm onunla gelir. pnpm **gerekmez** |
| [Brave](https://brave.com) | güncel | Yan panel + mikrofon |
| Docker Desktop + WSL2 | isteğe bağlı | Yalnızca masaüstü (CUA) demosu |

**Başlat:**

1. Repo kökündeki **`start-polaris.cmd`** dosyasına çift tıkla.
   - İlk seferde kurulum yapar (`.venv`, Python paketleri, extension, whisper modeli ~460 MB): 5–15 dk.
   - `backend\.env` yoksa oluşturup **Not Defteri'nde açar** → `DEEPSEEK_API_KEY` (zorunlu) ve
     `BRAVE_API_KEY` (web araması) yaz, **kaydet, Not Defteri'ni kapat**; kurulum devam eder.
   - Extension'ı derler, backend'i başlatır ve `/health` yanıt verene kadar bekler.
   - Backend **sistem tepsisinde** çalışır: saatin yanındaki **Polaris yıldızı** simgesi
     (sağ tık → durum / çıkış). Tepsi uygulaması kullanılamıyorsa ayrı bir "Polaris Backend" konsol
     penceresi açılır (`start-polaris.cmd --console` ile konsol modu zorlanır).
2. Brave'e yükle (pencere aynı adımları ve klasör yolunu yazar; yol panoya kopyalanır):
   1. Adres çubuğuna `brave://extensions`
   2. Sağ üstte **Geliştirici modu**nu aç
   3. **Paketlenmemiş uzantıyı yükle** → `extension\dist` klasörünü seç
      (sürüm zip'inde: `extension-dist`)
   4. Polaris simgesine tıkla → yan panel açılır
   5. İlk **Bas-konuş**'ta mikrofon iznini ver
   6. Panel **Ayarlar → Backend adresi**: `http://localhost:8765`
3. Panelde yaz ya da bas-konuş: *"Bu sayfayı özetle"*.

Doğrulama: <http://127.0.0.1:8765/health> → `{"ok": true, ...}` · Tam kontrol listesi:
`.venv\Scripts\python scripts\doctor.py`

### Elle kurulum (geliştirici)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -DownloadModels   # idempotent; -WithCua, -WithDev, -NoTray
.\scripts\run-backend.ps1                 # geliştirme: uvicorn --reload; demo: -NoReload (python -m backend); -Port 8010
.\scripts\build-extension.ps1             # npm install (gerekirse) + tsc + vite build -> extension\dist
.\.venv\Scripts\python scripts\doctor.py
```

Kod değiştirdikten sonra `build-extension.ps1`'i tekrar çalıştır ve `brave://extensions` içinde uzantıyı yenile.

### Testler

```powershell
.\.venv\Scripts\python -m pytest -q tests --ignore=tests/e2e    # backend birim testleri
.\.venv\Scripts\python scripts\e2e\run.py                       # sahte LLM + sahte extension ile uçtan uca
cd extension; npx tsc --noEmit; npx vitest run; npx vite build  # extension
```

Aynı adımlar her push/PR'da GitHub Actions'ta çalışır (`.github/workflows/ci.yml`); derlenmiş
`dist` artifact olarak indirilebilir.

### Sürüm paketi

```powershell
.\scripts\package-release.ps1
# -> release\polaris-v<sürüm>.zip            extension-dist, backend, scripts, shared, docs, demo, tray,
#                                            README, CHANGELOG, start-polaris.cmd, Polaris.exe (varsa)
# -> release\polaris-extension-v<sürüm>.zip  yalnızca derlenmiş extension
```

Sürüm `extension/manifest.json` içinden okunur. Paket `.env`, `backend/data/` ve `__pycache__` içermez;
`release/` git'e girmez. Değişiklikler: [`CHANGELOG.md`](CHANGELOG.md).

---

## Ortam değişkenleri (`backend/.env`)

`backend/.env.example` kopyalanır (start-polaris / setup bunu yapar). Gerçek anahtarları **asla** git'e
ekleme (`.env` .gitignore'dadır). `.env` değişince backend'i yeniden başlat.

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | **Zorunlu.** LLM anahtarı |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI uyumlu başka bir ağ geçidi için değiştir |
| `LLM_MODEL` | `deepseek-chat` | Model adı. EVREN geçidinde önerilen: `gemma-4-31b` (hızlı) |
| `LLM_FALLBACK_MODEL` | boş | Birincil model hata verirse (zaman aşımı, 5xx, model yok) düşülecek model, örn. `deepseek-v4.1-flash` |
| `LLM_THINKING` | açık | `off`: birincil modelin gizli "düşünme" adımını kapatır (Gemma'da adım ~1 sn, ~5 kat hızlı) |
| `BRAVE_API_KEY` | boş | `web.search` için ([brave.com/search/api](https://brave.com/search/api/)); boşsa anahtarsız DuckDuckGo yedeği kullanılır |
| `WEB_SEARCH_FALLBACK` | `duckduckgo` | `off` yapılırsa anahtarsız yedek kapanır |
| `STT_MODEL` | `small` | `small` (~460 MB) \| `base` (~140 MB, ~2× hızlı, Türkçede daha zayıf) |
| `STT_CPU_THREADS` | mantıksal çekirdek/2 | Çekirdek sayısının tamamını verme (yavaşlatır) |
| `TTS_VOICE` | `tr-TR-EmelNeural` | edge-tts sesi |
| `TIMEZONE` | `Europe/Istanbul` | Hatırlatıcılar için |
| `HOST` / `PORT` | `127.0.0.1` / `8765` | Backend adresi. `PORT` ortam değişkeni `.env`'i ezer |
| `CUA_ENABLED` | `false` | Masaüstü araçlarını açar (Docker gerekir) |
| `DESKTOP_PARSER` | `ocr` | `ocr` \| `omniparser` |
| `ASSISTANT_DB` | `backend/data/assistant.db` | Test için ayrı veritabanı |
| `LLM_TIMEOUT_S` / `TOOL_TIMEOUT_S` / `CONFIRM_TIMEOUT_S` | 30 / 20 / 60 | Zaman aşımları |
| `MAX_TOOL_CALLS` / `REQUEST_BUDGET_S` / `MAX_REVISIONS` | 8 / 90 / 2 | İstek başına bütçe ve revizyon sınırı |
| `REMINDER_POLL_S` | `30` | Hatırlatıcı tarama aralığı |

Loglar: `backend/data/logs/polaris.log`.

---

## Demo senaryoları

Tam akış, beklenen zaman çizelgesi adımları ve ters giderse planları: [`demo/script.md`](demo/script.md).

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
| `pnpm install` **EPERM** (esbuild) | Proje pnpm kullanmaz. `extension\node_modules`'u sil, `cd extension; npm install --no-package-lock` (ya da `build-extension.ps1 -Install`) |
| **Port 8765 dolu** | start-polaris hangi PID'in kullandığını yazar. O programı kapat (Docker/WSL konteyneri de olabilir: `wslrelay`) **ya da** `backend\.env` içine `PORT=8010` yaz (veya `set PORT=8010` sonra `start-polaris.cmd`) ve panel **Ayarlar → Backend adresi** `http://localhost:8010` yap |
| Panel "Bağlantı yok" | Backend çalışıyor mu? `/health` aç. Ayarlardaki adres ve port doğru mu? |
| **Mikrofon izni** yok / reddedildi | `brave://settings/content/microphone` → uzantıya izin ver; ya da panelde yeniden **Bas-konuş** → izin sayfası. Mikrofon çalışmazsa metin kutusu her zaman kullanılabilir |
| İlk sesli istek ~60 sn sürüyor | İlk `/stt` whisper `small` modelini (**~460 MB**) indirir. Önceden: `scripts\setup.ps1 -DownloadModels`. Hız için `STT_MODEL=base`, `STT_CPU_THREADS=8`; demoda reload kapalı (`-NoReload`) |
| `/tts` **403/503** (edge-tts) | edge-tts resmî olmayan istemcidir; kod bir kez yedek uç noktayla dener, yine olmazsa panel **tarayıcının kendi sesine** düşer. Cevap metni her zaman görünür. Kalıcıysa `pip install -U edge-tts` |
| Cevap gelmiyor / LLM hatası | `DEEPSEEK_API_KEY`, `LLM_MODEL`, `DEEPSEEK_BASE_URL` doğru mu? `.env` sonrası backend yeniden başlatıldı mı? (`/health` → `llm_configured`) |
| `web.search` hata | Anahtar yoksa DuckDuckGo yedeği kullanılır; o da hata verirse internet bağlantısını kontrol et. `BRAVE_API_KEY` ile 429 = aylık limit |
| `extension/dist` yok / derleme hatası | `.\scripts\build-extension.ps1 -Install`; Node 18+ kurulu mu? |
| Uzantı sayfayı okuyamıyor | `brave://` ve mağaza sayfalarında content script çalışmaz; normal bir `https://` sekmesi aç, sayfayı yenile |
| `desktop.*` araçları yok | `CUA_ENABLED=true`, Docker Desktop açık, `setup.ps1 -WithCua`; bkz. [`docs/cua-setup.md`](docs/cua-setup.md) |
| Hatırlatıcı saati kayık | `tzdata` kurulu mu, `TIMEZONE` doğru mu? |
| Kurulum yarıda kaldı | `.venv` klasörünü sil, `start-polaris.cmd`'yi yeniden çalıştır |
| Python 3.14 sorunları | `py -3.12 -m venv .venv` ile yeniden kur (ya da `setup.ps1 -PythonExe ...`) |
| "Betik çalıştırma devre dışı" | `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` (start-polaris bunu kendisi yapar) |

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
scripts/           setup / run-backend / build-extension / package-release / doctor + e2e, smoke, mock
tray/              Sistem tepsisi uygulaması (Polaris.exe, python -m tray)
start-polaris.cmd  Çift tıkla kur + başlat
.github/workflows/ CI: pytest + e2e, tsc + vitest + vite build
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
