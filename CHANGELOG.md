# Değişiklik Günlüğü

Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) esaslıdır; sürümler
[Semantik Sürümleme](https://semver.org/lang/tr/) izler. Sürüm numarası
`extension/manifest.json` ve `backend/config.py` (`APP_VERSION`) ile aynıdır.

## [0.2.0] — 2026-10-13

### Kurulum
- **Kurulum sihirbazı `PolarisSetup-0.2.0.exe`:** İleri → Kur → Bitir. Yönetici izni ve
  önceden kurulu Python gerekmez (Python'u kendisi indirir). Konsol penceresi açılmaz,
  Türkçe ilerleme adımları gösterir. Masaüstü ve Başlat menüsü kısayolu, "Onar",
  "Brave rehberi", Program Ekle/Kaldır girdisi. Brave yoksa kurmayı önerir.
  Üstüne kurulunca API anahtarı ve ayarlar korunur.

### Masaüstü
- **Polaris penceresi:** Brave olmadan sohbet, ses, arama, hatırlatıcı. **Alt+Space** ile
  aç/kapa; kapatınca tepside çalışmaya devam eder.
- **Masaüstü maskotu:** ekranın köşesinde şeffaf, sürüklenebilir altın yıldız. Tek tıkla
  görev balonu (yaz ya da basılı tutup konuş), çift tıkla Polaris penceresi. Polaris
  çalışırken kuyruklu yıldıza dönüşür, hatırlatmada sallanır.

### Asistan
- **Sohbet hafızası:** devam soruları anlaşılır ("peki İzmir'in?"); sohbet kapatıp açınca
  geri gelir; "Yeni sohbet" düğmesi. Kişisel bilgiler ("benim adım…") kalıcı hafızaya
  alınır; şifre/kart/IBAN/TC kimlik asla kaydedilmez; "bunu unut" ile silinir.
- **Tarayıcıda görünür el:** altın yıldız imleç hedefe kayar, tıklar, harf harf yazar ve
  ne yaptığını söyler. Siteleri yeni sekmede kendisi açar (`browser.open`).
- **İnternetsiz Türkçe ses:** EMA Lightning (34 MB) varsayılan TTS; 4,6 sn konuşma ~0,9 sn'de
  üretilir. Olmazsa edge-tts'e geçer.
- **Varsayılan model Gemma 4 31B** (EVREN), düşünme modu kapalı. Uzun görevler için
  süre sınırı 15 dk, araç çağrısı sınırı 40.
- Arkadaşımızın maskotu (PolarisPet) panelde de: başlıkta, karşılamada ve çalışırken.

## [0.1.1] — 2026-10-13

### Eklendi
- **Panelden yapay zeka ayarları:** Ayarlar → "Yapay zeka" bölümünde sağlayıcı
  (EVREN · DeepSeek · Diğer), API anahtarı (göster/gizle, tek tıkla yapıştır),
  model kartları, yedek model ve "Hızlı mod". "Bağlantıyı test et" gerçek bir
  deneme çağrısı yapar; "Kaydet" sunucuyu yeniden başlatmadan devreye girer.
  `.env` dosyasını elle düzenlemek gerekmez.
- İlk açılışta anahtar yoksa uyarı kartındaki **"Anahtarı gir"** doğrudan anahtar alanını açar.
- Başlıkta seçili modelin adı görünür (örn. "Gemma 4 31B").
- Port yedeği: tepsi uygulaması varsayılan port doluysa sıradaki boş portu seçer,
  panel Polaris'i kendisi bulur (`/health` → `service: "polaris"`).
- Masaüstü (CUA) görevlerinde ajan sonucu ekran görüntüsüyle doğrulamadan bitirmez;
  Xfce klavye kısayollarını kullanır.

### Güvenlik
- `/settings/*` uçları yalnızca bu bilgisayardan ve Polaris eklentisinden erişilebilir.
- Anahtar hiçbir yanıtta dönmez (yalnızca son 4 karakter); tarayıcıda saklanmaz.
- Kayıtlı anahtar yalnızca kayıtlı adrese veya EVREN/DeepSeek'in resmi adreslerine gönderilir.

## [0.1.0] — 2026-10-13

İlk kullanıma hazır sürüm.

### Doğrulama (gerçek sistemle)
- Gerçek Chromium + yüklü eklenti + gerçek model (EVREN `gemma-4-31b`) ile canlı
  testler 8/8: Vikipedi sayfa özeti, en ucuz 3 kitap (books.toscrape.com),
  Vikipedi'de arama, anahtarsız web araması. `node scripts/browser_e2e/run.mjs --live`
- Ses uçtan uca 19/19 (bas-konuş → STT → ajanlar → hatırlatıcı → TTS):
  `node scripts/browser_e2e/run.mjs --voice`
- Masaüstü (CUA) gerçek Docker sandbox'ında doğrulandı (`scripts/cua_smoke.py`).

### Model ve hız
- `LLM_MODEL=gemma-4-31b` + `LLM_THINKING=off` ile ajan adımı ~1 sn;
  `LLM_FALLBACK_MODEL` (örn. `deepseek-v4.1-flash`) birincil model hata verirse devreye girer.
- Metin olarak yazılan araç çağrıları (Gemma) otomatik ayrıştırılır.
- `BRAVE_API_KEY` yoksa web araması anahtarsız DuckDuckGo yedeğiyle çalışır.
- Varsayılan port **8765** (8000 çoğu makinede WSL/Docker/geliştirme sunucularıyla çakışıyor).

### Asistan

- **Brave yan paneli** (MV3, React + TypeScript): sohbet, Markdown cevaplar, kaynak
  bağlantıları, ayarlar (backend adresi), açık/koyu tema.
- **Sesli kullanım:** bas-konuş → `POST /stt` (faster-whisper, Türkçe, CPU/int8) →
  cevap `POST /tts` (edge-tts, `tr-TR-EmelNeural`, önbellekli mp3). edge-tts
  erişilemezse panel tarayıcının kendi sesine düşer.
- **Multi-agent orkestratör:** Router → Planner → Executor ⇄ Araçlar → Judge (en çok 2
  revizyon) → Responder. Tek LLM (DeepSeek / OpenAI uyumlu), ajan başına ayrı prompt ve
  JSON şeması; istek başına araç/süre bütçesi.
- **Açıklanabilirlik (XAI):** her adım bir `TraceStep` (karar, gerekçe, kanıt, güven,
  gecikme); panelde **"Neden?"** zaman çizelgesi, `ref` kanıtına tıklayınca sayfada
  vurgulama. İzler SQLite'ta saklanır (`/traces`).
- **Tarayıcı computer-use:** DOM snapshot → numaralı öğe listesi → `click/type/read/
  navigate/scroll` (`ref` ile). Kısıtlı sayfalarda anlaşılır hata.
- **Masaüstü computer-use (CUA, isteğe bağlı):** Docker Linux sandbox, ekran görüntüsü
  → OCR → numaralı liste → `desktop.*` araçları; işaretli görüntüler `/shots`.
- **Araçlar:** `web.search` (Brave Search API), `memory.save/search`,
  `reminders.add/list` + arka planda zamanı gelen hatırlatıcılar için `notify`.

### Güvenlik

- **Onay kapısı:** gönder / satın al / öde / sil vb. aksiyonlar kullanıcı onayı olmadan
  çalışmaz; 60 sn içinde cevap yoksa reddedilmiş sayılır.
- Şifre alanlarına asla yazılmaz; sayfa içeriği LLM'e komut değil **veri** olarak verilir.
- API anahtarları yalnızca `backend/.env` içinde; backend varsayılan olarak yalnızca
  `127.0.0.1`'e bağlanır.

### Kurulum ve dağıtım

- **`start-polaris.cmd`** — çift tıkla başlat: ilk seferde kurulum (+ whisper modeli),
  `backend\.env` oluşturup Not Defteri'nde açma, extension derleme, backend'i başlatma,
  Brave "paketlenmemiş uzantı" adımlarını yazdırma. Port 8765 doluysa ne yapılacağını
  söyler; `PORT` ile başka port seçilebilir.
- **Sistem tepsisi uygulaması** (`Polaris.exe` / `python -m tray`): backend'i gizli
  çalıştırır, saatin yanında Polaris yıldızı simgesi.
- `python -m backend` giriş noktası (Türkçe başlık, eksik ayar uyarıları, log dosyası
  `backend/data/logs/polaris.log`).
- `scripts/setup.ps1` (idempotent; `-DownloadModels`, `-WithCua`, `-WithDev`),
  `run-backend.ps1` (`-Port`, `-NoReload`), `build-extension.ps1`, `doctor.py`
  (ortam kontrol listesi), `package-release.ps1` (`release\polaris-v0.1.0.zip` ve
  `release\polaris-extension-v0.1.0.zip`).
- Extension bağımlılıkları artık yalnızca **npm** ile kurulur
  (`npm install --no-package-lock`); pnpm Windows'ta esbuild için EPERM verdiğinden
  `pnpm-lock.yaml` / `pnpm-workspace.yaml` kaldırıldı.

### Test ve CI

- Backend birim testleri (pytest), sahte LLM + sahte extension ile uçtan uca senaryolar
  (`scripts/e2e/run.py`), extension birim testleri (vitest).
- GitHub Actions: Python 3.12 (Windows) pytest + e2e; Node 22 tsc + vitest + vite build,
  derlenmiş `dist` artifact olarak yüklenir; PowerShell betiklerinin sözdizimi kontrolü.

### Bilinen sınırlamalar

- İlk sesli istekte whisper `small` modeli (~460 MB) indirilir (`setup.ps1
  -DownloadModels` ile önceden indirilebilir).
- edge-tts resmî olmayan bir istemcidir; 403/503 durumunda tarayıcı sesine düşülür.
- Masaüstü (CUA) Docker Desktop + WSL2 gerektirir; Windows 10 Home'da Windows Sandbox yoktur.
