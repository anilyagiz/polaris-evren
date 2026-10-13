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

## Kurulum (Windows)

**Önerilen yol: kurulum sihirbazı.** Python, Node.js veya yönetici izni **gerekmez**.

1. [GitHub Releases](https://github.com/anilyagiz/polaris-evren/releases) sayfasından
   **`PolarisSetup-<sürüm>.exe`** dosyasını indirip çalıştırın (zip'leri değil).
   - SmartScreen "Windows bilgisayarınızı korudu" derse: **Ek bilgi → Yine de çalıştır**.
2. Sihirbaz (Türkçe): klasör (`%LOCALAPPDATA%\Programs\Polaris`) → seçenekler
   (*Masaüstü kısayolu*, *Ses modelini şimdi indir ~460 MB*, *Windows açılışında başlat*)
   → Brave yoksa *Brave tarayıcısını da kur* → **Kur**.
3. Kurulum, Polaris klasörüne kendi **Python 3.12**'sini indirir (uv ile; sistem Python'una
   dokunmaz), paketleri kurar ve ilerlemeyi sihirbazda gösterir (internet hızına göre 3–10 dk).
   İnternet kesilirse: *Yeniden dene* ya da sonra **Başlat → Polaris → Polaris'i onar**.
4. Bitişte **Polaris başlar** (saatin yanında yıldız simgesi) ve **Brave kurulum rehberi** açılır:
   `brave://extensions` → Geliştirici modu → *Paketlenmemiş öğe yükle* → rehberdeki
   *Klasör yolunu kopyala* → sabitle → **Ayarlar → Yapay zeka**'ya API anahtarını yapıştır.
5. Panelde yaz ya da bas-konuş: *"Bu sayfayı özetle"*.

Kaldırma: **Ayarlar → Uygulamalar → Polaris** ya da Başlat → Polaris → *Polaris'i kaldır*
(`.venv`, Python, kısayollar ve açılış kaydı silinir; konuşma geçmişini saklamak isteyip
istemediğiniz sorulur). Ayrıntılar ve sessiz kurulum: [`docs/INSTALL.md`](docs/INSTALL.md).

### İleri düzey: zip ile kurulum

`polaris-v<sürüm>.zip`'i açıp **`start-polaris.cmd`**'ye çift tıklayın. Bu yol sistemde
**Python 3.12** (3.11–3.13; 3.14 değil, "Add python.exe to PATH") ister; ilk seferde `.venv`,
paketler ve whisper modeli kurulur (5–15 dk), backend tepsi uygulamasıyla başlar ve Brave
adımları pencerede yazılır (eklenti klasörü: `extension-dist`). `start-polaris.cmd --console`
konsol modunu zorlar. Docker Desktop + WSL2 yalnızca isteğe bağlı masaüstü (CUA) demosu içindir.

Doğrulama: <http://127.0.0.1:8765/health> → `{"ok": true, "service": "polaris", ...}`
(8765 doluysa Polaris 8766+ portuna geçer; panel kendisi bulur) · Tam kontrol listesi:
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
.\scripts\package-release.ps1              # -SkipBuild, -SkipTray, -NoInstaller
# -> release\PolarisSetup-<sürüm>.exe        kurulum sihirbazı (Inno Setup 6; installer\, docs\INSTALL.md)
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
| `DEEPSEEK_BASE_URL` | `https://evren-llmapi.ssyz.org.tr/v1` | EVREN ağ geçidi; başka bir OpenAI uyumlu geçit için değiştir |
| `LLM_MODEL` | `gemma-4-31b` | Model adı (Gemma 4 31B, hızlı). Panelden Ayarlar → Yapay zeka ile değiştirilebilir |
| `LLM_FALLBACK_MODEL` | boş | Birincil model hata verirse (zaman aşımı, 5xx, model yok) düşülecek model, örn. `deepseek-v4.1-flash` |
| `LLM_THINKING` | `off` | Gizli "düşünme" adımı. `off` (varsayılan) Gemma'da adımı ~1 sn yapar; `on` ile açılır |
| `BRAVE_API_KEY` | boş | `web.search` için ([brave.com/search/api](https://brave.com/search/api/)); boşsa anahtarsız DuckDuckGo yedeği kullanılır |
| `WEB_SEARCH_FALLBACK` | `duckduckgo` | `off` yapılırsa anahtarsız yedek kapanır |
| `STT_MODEL` | `small` | `small` (~460 MB) \| `base` (~140 MB, ~2× hızlı, Türkçede daha zayıf) |
| `STT_CPU_THREADS` | mantıksal çekirdek/2 | Çekirdek sayısının tamamını verme (yavaşlatır) |
| `TTS_ENGINE` | `ema` | `ema`: çevrimdışı Türkçe [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (Apache-2.0, ~34 MB model ilk çağrıda iner; `torch` CPU kurulumu diskte ~500 MB ekler). `edge`: edge-tts (internet). EMA yüklenemezse otomatik edge-tts'e düşer |
| `TTS_SPEED` | `1.0` | EMA konuşma hızı (0.25–4) |
| `TTS_WARMUP` | boş | `1`: EMA modeli açılışta arka planda yüklenir (`STT_WARMUP=1` de tetikler) |
| `TTS_VOICE` | `tr-TR-EmelNeural` | edge-tts sesi (yedek motor) |
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
