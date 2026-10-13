# Kişisel Asistan — Kurulum README (Kişi 5)

Brave tarayıcısının **yan panelinde** çalışan; sesli, açıklanabilir (zaman çizelgesi) ve
tarayıcı/masaüstünü kullanabilen kişisel asistan. Bu belge sistemi **sıfırdan
~15 dakikada** ayağa kaldırmak için yeterlidir.

> Sahiplik: Bu README, `scripts/test_*.py`, `backend/voice/*`,
> `backend/tools/{search,memory,reminders}.py` ve `backend/db.py` → **Kişi 5**.
> `backend/main.py` ve `backend/tools/registry.py` şu an **geçici iskelettir**
> (asıl sahipleri Kişi 3).

---

## 1. Gereksinimler

| Şey | Not |
|---|---|
| Windows 10/11 | Demo makinesi. Windows 10 Home'da *Windows Sandbox* yoktur. |
| Python **3.11+** | 3.12/3.13 ile test edildi (`py -3.12 -m venv .venv`). |
| Node.js 18+ | Extension'ı derlemek için (`extension/`). |
| Brave tarayıcı | Yan panel + mikrofon izni. |
| İnternet | EVREN/DeepSeek LLM, Brave Search API, edge-tts, whisper model indirme. |
| Docker Desktop + WSL2 | *İsteğe bağlı* — yalnızca masaüstü (CUA) demosu için. |

---

## 2. `.env` dosyasını doldur (2 dk)

```powershell
cd assistant\backend
copy .env.example .env
```

`.env` içeriği ve değerler:

| Değer | Ne yazılır | Nereden alınır |
|---|---|---|
| `DEEPSEEK_API_KEY` | `evren_llm_...` | EVREN panelinden aldığın API anahtarı |
| `DEEPSEEK_BASE_URL` | `https://evren-llmapi.ssyz.org.tr/v1` | Hazır. **Sonuna `/v1` ekleme**, openai SDK'ya olduğu gibi verilir. |
| `LLM_MODEL` | `deepseek-v4.1-flash` | EVREN → Models |
| `BRAVE_API_KEY` | `BSA...` | https://brave.com/search/api/ (ücretsiz katman: aylık 2.000 istek) |
| `STT_MODEL` | `small` | CPU'da hız için `small` kal; `base` dene ama yavaştır. |
| `TTS_VOICE` | `tr-TR-EmelNeural` | Hazır |
| `TIMEZONE` | `Europe/Istanbul` | Hazır |
| `CUA_ENABLED` | `false` | Masaüstü demosu açıksa `true` |
| `DESKTOP_PARSER` | `ocr` | `ocr` \| `omniparser` |

> `.env` değiştikten sonra **uvicorn'ı yeniden başlatın** (ortam değişkenleri süreç
> başında bir kez okunur).

---

## 3. Backend'i ayağa kaldır (5 dk)

Repo **kökünden** çalıştırın (`backend` bir pakettir):

```powershell
cd C:\Kullanıcılar\<sen>\assistant
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Doğrulama: tarayıcıda <http://localhost:8000/health> → `{"ok": true}`

İlk `POST /stt` çağrısında faster-whisper modeli (~460 MB, `small`) indirilir;
bu normaldir, bir kez olur ve bellekte kalır.

---

## 4. Extension'ı Brave'e yükleme (5 dk)

> Extension `extension/` klasörü **Kişi 1/2**'dedir; `manifest.json`,
> `vite.config.ts` ve `src/**` onlara aittir.

```powershell
cd extension
npm install
npm run build        # çıktısı extension/dist
```

1. Brave'de `brave://extensions` adresini aç.
2. Sağ üstteki **Geliştirici modu**nu aç.
3. **Paketlenmemiş uzantıyı yükle** → `extension\dist` klasörünü seç.
4. Paneldeki **Ayarlar** → Backend adresi: `http://localhost:8000` (varsayılan bu).

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
- Testler `backend/data/` içine yazar (db önbelleği, örnek mp3).

### Ses gecikmesi (ölçüm — Ryzen 7 4800H / 16 mantıksal çekirdek, 5.8 sn'lik örnek ses)

| Adım | Hedef | Ölçüm |
|---|---|---|
| STT `small` — ilk çağrı (model yüklenir) | — | 4.98 sn |
| STT `small` — warm (model bellekte) | < 3 sn | **2.90 sn ✓** |
| STT `base` — ilk çağrı | — | 2.08 sn |
| STT `base` — warm | < 3 sn | **0.93 sn ✓ (demo için önerilir)** |
| TTS — ilk çağrı (edge-tts, ağ) | < 1.5 sn | ~1–4 sn (429 alınırsa +3 sn tekrar) |
| TTS — önbellekli | < 0.2 sn | 0.003–0.005 sn ✓ |
| Uçtan uca: bas-konuş → sesli cevap | < 8 sn | *(panelle entegrasyonda ölçülecek)* |

**Not:** Hedefe `base` ile rahatça ulaşılır; doğruluk küçük bir düşüş görür
("testi" → "deste" gibi). Demo için `backend/.env` içinde `STT_MODEL=base` önerilir,
sözleşmeli varsayılan `small` korunmuştur. Örnek webm + Türkçe cümle mp3 çevirisi
`python scripts\test_voice.py` ile otomatik doğrulanır.

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
| `/stt` çok yavaş (5 sn ses > 3 sn) | `STT_MODEL=small` kullan (`base` daha yavaştır). İlk çağrı model indiriyor olabilir; ikinciyi dene. |
| `/stt` 500 döndü | Dosya biçimi webm/opus olmalı (PyAV çözer, ffmpeg gerekmez). `python scripts\test_voice.py` ile ayrı ayrı doğrula. |
| `/tts` **503** | edge-tts resmî olmayan istemci ve internet ister. **Not:** Microsoft 2026-10'da sentesi taşıdığı için eski sürümde 403 çıkabilir; `voice/tts.py` bu geçici düzeltmeyi (yeni uç nokta) otomatik uygular — bkz. [edge-tts#490](https://github.com/rany2/edge-tts/issues/490). Yine de 503 gelirse bağlantı/proxy kontrolü; panel otomatik olarak tarayıcı sesine geçer. |
| `BRAVE_API_KEY ayarlanmamış` | `.env` içinde doldur ve **uvicorn'ı yeniden başlat**. |
| `web.search` boş dönüyor | `.env` içinde `BRAVE_API_KEY` var mı, **uvicorn yeniden başlatıldı mı**? |
| Brave **429** | Aylık istek limiti doldu; bekle veya `count` değerini düşür. |
| Panel "Bağlantı yok" | uvicorn çalışıyor mu? `http://localhost:8000/health`. Ayarlardaki backend adresini kontrol et. |
| Mikrofon izni reddedildi | `brave://settings/content/microphone` → siteye izin ver. |
| `ZoneInfo` hatası (reminders) | `pip install tzdata` (requirements.txt'de var). |
| Python 3.13 + pip tuhaflıkları | `py -3.12 -m venv .venv` kullan (bu demoda öyle kuruldu). |
| `/ws` 404 | `/ws` WebSocket endpoint'i **Kişi 3**'tedir; şu an yalnızca ses/araç uç noktaları çalışır. |

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
