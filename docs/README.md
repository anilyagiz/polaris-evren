# Kişisel Asistan — Kurulum README (Kişi 5)

Brave tarayıcısının **yan panelinde** çalışan; sesli, açıklanabilir (zaman çizelgesi) ve
tarayıcı/masaüstünü kullanabilen kişisel asistan. Bu belge sistemi **sıfırdan
~15 dakikada** ayağa kaldırmak için yeterlidir.

> **Hızlı yol:** Kökteki `start-polaris.cmd` dosyasına çift tıklayın (kurulum + başlatma). Ayrıntı: kök [`README.md`](../README.md), `scripts/setup.ps1`,
> `scripts/run-backend.ps1`, `scripts/build-extension.ps1`, `scripts/doctor.py`. Mimari: [`ARCHITECTURE.md`](ARCHITECTURE.md).
> Bu belge ses/araç (K5) ayrıntılarını ve ölçümleri içerir.

> Sahiplik: Bu README, `scripts/test_*.py`, `backend/voice/*`,
> `backend/tools/{search,memory,reminders}.py` ve `backend/db.py` → **Kişi 5**.
> `backend/main.py` ve `backend/tools/registry.py` şu an **geçici iskelettir**
> (asıl sahipleri Kişi 3). Devir notları: [`docs/HANDOFF-K3.md`](HANDOFF-K3.md).

---

## 1. Gereksinimler

| Şey | Not |
|---|---|
| Windows 10/11 | Demo makinesi. Windows 10 Home'da *Windows Sandbox* yoktur. |
| Python **3.11–3.13** | `py -3.12 -m venv .venv` ile kuruldu ve test edildi. **Python 3.14 kullanmayın**: `faster-whisper`'ın bağımlılığı `ctranslate2` için 3.14 tekerleği (wheel) henüz güvenilir değil, derleme denemesi zaman kaybettirir. |
| Node.js 18+ | Extension'ı derlemek için (`extension/`). |
| Brave tarayıcı | Yan panel + mikrofon izni. |
| İnternet | EVREN/DeepSeek LLM, Brave Search API, edge-tts, whisper model indirme. |
| Docker Desktop + WSL2 | *İsteğe bağlı* — yalnızca masaüstü (CUA) demosu için. |

> **Dikkat:** Bu makinede `py` komutunun varsayılanı 3.14, `python` ise 3.13'tür.
> Sanal ortamı **her zaman sürümü açıkça yazarak** kurun (`py -3.12 -m venv .venv`),
> yoksa `python -m venv` yanlış sürümü seçebilir.

---

## 2. `.env` dosyasını doldur (2 dk)

```powershell
cd <repo-kökü>\backend
copy .env.example .env      # veya: scripts\setup.ps1 bunu otomatik yapar
```

`.env` içeriği ve değerler:

| Değer | Ne yazılır | Nereden alınır |
|---|---|---|
| `DEEPSEEK_API_KEY` | `evren_llm_...` | EVREN panelinden aldığın API anahtarı |
| `DEEPSEEK_BASE_URL` | `https://evren-llmapi.ssyz.org.tr/v1` | **K3 onayı bekliyor** — ORTAK BAĞLAM §4 farklı. Bkz. HANDOFF-K3.md |
| `LLM_MODEL` | `deepseek-v4.1-flash` | **K3 onayı bekliyor** — ORTAK BAĞLAM §4'te boş. |
| `BRAVE_API_KEY` | `BSA...` | https://brave.com/search/api/ (ücretsiz katman: aylık 2.000 istek) |
| `STT_MODEL` | `small` | Sözleşmeli varsayılan. Yavaş kalırsa `base`'e düşülebilir (**base daha hızlıdır**, bkz. §6). |
| `STT_CPU_THREADS` | `8` | Boş bırakılırsa mantıksal çekirdek/2. Mantıksal çekirdek sayısını vermek CTranslate2'de oversubscription yapıp **yavaşlatır**. |
| `TTS_VOICE` | `tr-TR-EmelNeural` | Hazır |
| `TIMEZONE` | `Europe/Istanbul` | Hazır |
| `ASSISTANT_DB` | *(boş)* | İsteğe bağlı; testler için ayrı veritabanı yolu. Boşsa `backend/data/assistant.db`. |
| `CUA_ENABLED` | `false` | Masaüstü demosu açıksa `true` |
| `DESKTOP_PARSER` | `ocr` | `ocr` \| `omniparser` |

> `.env` değiştikten sonra **uvicorn'ı yeniden başlatın** (ortam değişkenleri süreç
> başında bir kez okunur).

---

## 3. Backend'i ayağa kaldır (5 dk)

Repo **kökünden** çalıştırın (`backend` bir pakettir):

```powershell
cd <repo-kökü>
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # venv + paketler + .env + npm install (-DownloadModels: whisper modeli)
.\scripts\run-backend.ps1                                      # uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
# elle: py -3.12 -m venv .venv ; .venv\Scripts\activate ; pip install -r backend\requirements.txt
```

Doğrulama: tarayıcıda <http://localhost:8765/health> → `{"ok": true}`

İlk `POST /stt` çağrısında faster-whisper modeli (~460 MB, `small`) indirilir;
bu normaldir, bir kez olur ve bellekte kalır (ilk çağrı ~4,8 sn; sonrakiler ~2,1 sn).

---

## 4. Extension'ı Brave'e yükleme (5 dk)

> Extension `extension/` klasörü **Kişi 1/2**'dedir; `manifest.json`,
> `vite.config.ts` ve `src/**` onlara aittir.

```powershell
.\scripts\build-extension.ps1   # = cd extension; npm install --no-package-lock; npm run build -> extension/dist
```

1. Brave'de `brave://extensions` adresini aç.
2. Sağ üstteki **Geliştirici modu**nu aç.
3. **Paketlenmemiş uzantıyı yükle** → `extension\dist` klasörünü seç.
4. Paneldeki **Ayarlar** → Backend adresi: `http://localhost:8765` (varsayılan bu).

### Mikrofon izni

- Brave adres çubuğundaki **kilit/geometri simgesi → Site ayarları → Mikrofon: İzin ver**,
  ya da `brave://settings/content/microphone`.
- Panelde **Bas-konuş** düğmesine ilk tıklamada Brave izin penceresi açılır; **İzin ver**.
- Not: Brave içi `webkitSpeechRecognition` **desteklenmez** → ses kaydı backend'e
  gönderilir ve `POST /stt` ile yazıya çevrilir. Cevap `POST /tts` ile mp3 olarak okunur.

---

## 5. Kendi testlerini çalıştır (2 dk)

```powershell
.venv\Scripts\activate
python scripts\test_voice.py     # örnek webm → metin, cümle → mp3 (+önbellek)
python scripts\test_tools.py     # web.search, memory.*, reminders.* gerçek API ile
```

- `test_voice.py` örnek sesi `edge-tts` ile üretir; kendi dosyanı vermek için:
  `python scripts\test_voice.py yol\ara.webm`.
- `test_tools.py` için `.env` içinde `BRAVE_API_KEY` **zorunludur** ve internet gerekir.
- Test araçları **gerçek `assistant.db`'yi kirletmez**: `ASSISTANT_DB` geçici bir
  dosyaya yönlendirilir ve test sonunda silinir (`--keep` ile korunur).
  `test_voice.py` ise `backend/data/` altına örnek mp3 yazar.

### Ölçülen gecikmeler (Ryzen 7 4800H, 8 fiziksel çekirdek, 5,8 sn'lik örnek ses)

| Adım | Hedef | Ölçüm |
|---|---|---|
| STT `small` — ilk çağrı (model yüklenir) | — | 4,80–5,23 sn |
| STT `small` — warm (model bellekte, `cpu_threads=8`) | < 3 sn | **2,14 / 2,42 sn ✓** |
| STT `small` — warm (`cpu_threads` ayarsız, eski sürüm) | < 3 sn | 2,90 sn (marj %3 — kırılgan) |
| STT `base` — warm | < 3 sn | 0,93 sn |
| TTS — ilk çağrı (edge-tts, ağ) | < 1,5 sn | **1,19 sn ✓** |
| TTS — önbellekli | < 0,2 sn | 0,003–0,005 sn ✓ |
| Uçtan uca: bas-konuş → sesli cevap | < 8 sn | *(panelle entegrasyonda ölçülecek)* |

**Not:** `small` hedefi `cpu_threads=8` ile rahatça tutturuyor (~2,2 sn / 3,00 sn).
Yine de yavaş kalırsa `.env` içinde `STT_MODEL=base` yapın (~2× hız, Türkçe
doğrulukta küçük düşüş: "testi" → "deste" gibi). Sözleşmeli varsayılan `small`
korunmuştur. `--reload` açıkken her kod değişikliği modeli yeniden yükler; demo
sırasında `--reload` kullanmayın.

---

## 6. Masaüstü demosunu açma (CUA) — isteğe bağlı

1. **Docker Desktop + WSL2** kur ve doğrula: `docker run --rm hello-world`
2. CUA sandbox imajını hazırla (`trycua/cua`) — bu adımlar **Kişi 4**'tedir.
3. `backend/.env` içinde: `CUA_ENABLED=true`, `DESKTOP_PARSER=ocr`.
4. Backend'i yeniden başlat. Ekran görüntüleri `GET /shots/{name}` ile gelir ve
   zaman çizelgesinde `shot:/shots/...` olarak görünür.

> Not: Windows 10 Home'da *Windows Sandbox* yoktur; Docker Desktop + WSL2 kullanılır.

---

## 7. Sık sorunlar

| Belirti | Çözüm |
|---|---|
| `/stt` hedefi tutmuyor (5 sn ses > 3 sn) | Önce **`STT_CPU_THREADS=8`** olduğundan emin ol (mantıksal çekirdek sayısını vermek yavaşlatır). İlk çağrı model indiriyor olabilir; ikinciyi dene. Hâlâ yavaşsa `STT_MODEL=base` yap — **base small'dan ~2× hızlıdır** (0,93 sn vs ~2,2 sn), doğruluk biraz düşer. |
| `/stt` 500 döndü | Dosya biçimi webm/opus olmalı (PyAV çözer, ffmpeg gerekmez). `python scripts\test_voice.py` ile ayrı ayrı doğrula. |
| `/tts` **503** | edge-tts resmî olmayan istemci ve internet ister. İlk denemede başarısız olursa kod bir kez **yedek uç nokta** ile tekrarlar (bkz. `voice/tts.py::_apply_edge_workaround`). Varsayılan uç nokta bu makinede doğrulandı (1,19 sn). Yine 503 gelirse bağlantı/proxy kontrolü; panel otomatik olarak tarayıcı sesine geçer. |
| TTS için ikinci bir motor yok mu? | **Bilinçli tercih.** `gTTS` denendi ama `click<8.2` istiyor; `huggingface-hub` (faster-whisper'ın model indiricisi) ise `click>=8.4.2` istiyor. İkisi aynı ortamda çalışmıyor ve whisper'ın model indirmesi bozuluyor. Sözleşmenin öngördüğü yedek yol panelin kendi tarayıcı sesidir. |
| `memories`/`reminders` tablosu eski şemada kalmış | Kod ilk kullanımda **otomatik göç ettirir**: `id TEXT` → `id INTEGER`, eksikse `text_norm` eklenip doldurulur. Eski kayıtlar korunur, log'da `rebuilding the table with an integer id` görünür. |
| `BRAVE_API_KEY ayarlanmamış` | `.env` içinde doldur ve **uvicorn'ı yeniden başlat**. |
| `web.search` boş dönüyor | `.env` içinde `BRAVE_API_KEY` var mı, **uvicorn yeniden başlatıldı mı**? |
| Brave **429** | Aylık istek limiti doldu; bekle veya `count` değerini düşür. `web.search` anlaşılır bir hata döner, otomatik tekrar denemez. |
| Panel "Bağlantı yok" | uvicorn çalışıyor mu? `http://localhost:8765/health`. Ayarlardaki backend adresini kontrol et. |
| Mikrofon izni reddedildi | `brave://settings/content/microphone` → siteye izin ver. |
| Hatırlatıcı 3 saat erken/geç geliyor | `tzdata` kurulu mu (`pip install tzdata`)? `TIMEZONE` çözülemezse kod sabit +03:00'a düşer ve **uyarı loglar**; UTC'ye düşmez. |
| `ZoneInfo` hatası (reminders) | `pip install tzdata` (requirements.txt'de var). |
| Python sürümü / pip tuhaflıkları | `py -3.12 -m venv .venv` kullan (bu demoda öyle kuruldu). Python 3.14'ten kaçın. |
| `/ws` 404 | Eski sürüm kalıntısı olabilir; güncel `backend.main` `/ws`'i sunar. `python scripts\doctor.py` çalıştır. |
| `GET /v1/models` 404 logu | Sözleşmede `/v1` proxy'si yoktur; LLM çağrıları backend içinde (K3 `llm.py`) yapılır, panel yalnızca `/ws` kullanır. |

---

## 8. API özeti (sahiplik dahil)

| Metot | Yol | Sahip | Tanım |
|---|---|---|---|
| GET | `/health` | K3 | `{"ok": true}` |
| POST | `/stt` | **K5** | multipart `audio` → `{"text","language","duration_s"}` |
| POST | `/tts` | **K5** | `{"text"}` → `audio/mpeg` (önbellekli) |
| WS | `/ws` | K3 | Donmuş protokol (ORTAK BAĞLAM §6) |
| GET | `/shots/{name}` | K4 | İşaretli masaüstü ekran görüntüsü |

Tool adları: `web.search`, `memory.save`, `memory.search`,
`reminders.add`, `reminders.list` → hepsi `risk: safe`, `tools/registry.py`
üzerinden kayıtlıdır. `reminders` için ayrıca `async def due()` vardır; Kişi 3
bunu 30 saniyede bir çağırıp `notify` gönderir.

**Not:** `reminders.list` ve `due()` çıktısındaki `due_iso` alanı **yerel saat**
(`Europe/Istanbul`, örn. `2026-10-14T10:00:00+03:00`) biçimindedir; veritabanında
ise UTC saklanır. İkisi de geçerli ISO 8601'dir ve aynı anı gösterir.

**Not:** `memory.save` ve `reminders.add` dönen `id`, `memory.search` /
`reminders.list` çıktısındaki `id` alanları **tam sayıdır** (SQLite `INTEGER
PRIMARY KEY`). `reminders.list` yalnızca **henüz bildirilmemiş** hatırlatıcıları
döndürür; zamanı geçip `due()` ile işaretlenenler listeden düşer.

---

## 9. Kişi 3'e devir

Bu repoda K5'in dokunduğu **K3 sahipli** dosyalar ve yapılması gerekenler
`docs/HANDOFF-K3.md` içindedir. Özet: `backend/main.py` ve
`backend/tools/registry.py` geçici iskelettir, `.env.example` içindeki LLM
değerleri K3 onayı bekler.
