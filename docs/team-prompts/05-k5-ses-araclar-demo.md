# ROL PROMPTU — Kişi 5: Ses, Araçlar ve Demo

> Önce ORTAK BAĞLAM dosyasını verdim. Şimdi senin görevin bu.

Sen bu projede **Kişi 5**'in eşli programcısısın. Benim alanım: **sesi yazıya çevirme (STT)**, **yazıyı sese çevirme (TTS)**, **web arama**, **hafıza** ve **hatırlatıcılar**. Ayrıca README'yi ve demo senaryosunu ben hazırlıyorum.

## Çalışma şekli
- Önce en fazla 10 maddelik plan ver, onaylayınca kodla.
- Dosyaları **tam haliyle** ver, başında yolunu yaz.
- Her modül bağımsız çalışabilsin ve kendi küçük test script'i olsun.
- Sözleşmeyi (uç noktalar, tool adları, argümanlar) değiştirme.

## Sahip olduğum dosyalar
`backend/voice/stt.py`, `backend/voice/tts.py`, `backend/tools/search.py`, `backend/tools/memory.py`, `backend/tools/reminders.py`, `backend/db.py`, `scripts/test_voice.py`, `scripts/test_tools.py`, `docs/README.md`, `demo/script.md`.

## Arayüzlerim
- `voice/stt.py` ve `voice/tts.py` birer `APIRouter` dışa aktarır (`router`). Kişi 3 `main.py`'de include eder.
- Tool'lar Kişi 3'ün `tools/registry.py`'sindeki `Tool` ve `register` ile kaydedilir. Registry henüz yoksa aynı imzayla geçici bir taslak kullan.
- `tools/reminders.py` ayrıca `async def due() -> list[dict]` sağlar: zamanı gelmiş ve bildirilmemiş hatırlatmaları döndürür ve onları bildirildi olarak işaretler. Kişi 3 bunu 30 sn'de bir çağırıp `notify` gönderecek.

## Görevler (sırayla)

### Saat 1–2: STT
- `POST /stt`: multipart alan adı `audio` (audio/webm, opus).
- `faster-whisper`, model `STT_MODEL` (varsayılan `small`), `device="cpu"`, `compute_type="int8"`. Model uygulama ilk istekte bir kez yüklensin ve bellekte kalsın.
- `language="tr"`, `vad_filter=True`, `beam_size=1` (hız için).
- Gelen dosyayı geçici dosyaya yaz; faster-whisper webm'i PyAV ile çözebiliyor, sistemde ffmpeg gerekmiyorsa ekleme. Gerekiyorsa README'ye yaz.
- Dönüş: `{"text", "language", "duration_s"}`. Boş ses → `{"text": ""}`.
- Hedef: 5 sn'lik ses için CPU'da < 3 sn. Yavaşsa `base` modelini dene ve sonucu not et.

### Saat 2–3: TTS
- `POST /tts` JSON `{"text"}` → `audio/mpeg`.
- `edge-tts`, ses `TTS_VOICE` (varsayılan `tr-TR-EmelNeural`).
- Metni temizle: markdown işaretleri, URL'ler ve emoji çıkarılsın; 600 karakterle sınırla.
- `backend/data/tts_cache/{sha1}.mp3` önbelleği.
- Not: edge-tts resmî olmayan bir istemcidir ve internet ister. Hata olursa 503 dön; panel bu durumda tarayıcı sesine geçiyor.

### Saat 3–4: Araçlar
- `db.py`: `backend/data/assistant.db` için `get_conn()` yardımcı fonksiyonu, klasörü yoksa oluşturur.
- `tools/search.py` → `web.search {query, count?}`:
  - `GET https://api.search.brave.com/res/v1/web/search?q=…&count=…`, başlık `X-Subscription-Token: BRAVE_API_KEY`, `httpx` ile 10 sn timeout.
  - Dönüş `{results: [{title, url, snippet}]}`; snippet'teki HTML etiketlerini temizle.
  - 429 ve ağ hatalarında anlaşılır `{"ok": false, "error": …}`.
- `tools/memory.py` → `memory.save {text, tags?}`, `memory.search {query}`:
  - Tablo `memories(id, text, tags, created_at)`.
  - Arama: sorgudaki kelimelerle `LIKE` eşleşmesi, eşleşen kelime sayısına göre sırala, en fazla 5 sonuç. Türkçe küçük harfe çevirmeye dikkat et (`İ`→`i`, `I`→`ı`).
  - Kişi 3 için ayrıca `async def relevant(query, k=3) -> list[str]`.
- `tools/reminders.py` → `reminders.add {text, due_iso}`, `reminders.list {}`, ve `due()`:
  - Tablo `reminders(id, text, due_at, notified)`.
  - `due_iso` saat dilimi içermiyorsa `TIMEZONE` varsay. Geçmiş bir zaman ise hata dön.
- `scripts/test_tools.py`: her tool'u gerçek API ile bir kez çağırıp sonucu yazdırır.

### Saat 4–5: README ve demo hazırlığı
- `docs/README.md`: gereksinimler, `.env` doldurma, backend kurulumu (`python -m venv`, `pip install -r requirements.txt`, `uvicorn backend.main:app --reload --port 8000`), extension'ı Brave'e yükleme, mikrofon izni notu, CUA'yı açma, sık sorunlar.
- `demo/script.md`: ORTAK BAĞLAM §8'deki 6 senaryo için tam olarak söylenecek cümle, hangi sayfanın açık olacağı, beklenen trace adımları, beklenen cevap ve bir şey ters giderse ne yapılacağı. 5 dakikalık demo akışı.

### Saat 5–9: Uçtan uca ses ve demo
- Panelle birlikte ses akışını test et: bas-konuş → STT → cevap → TTS. Gecikmeyi ölç ve README'ye yaz.
- Demo senaryolarını sırayla çalıştır, sorunları sahiplerine bildir.
- 9,5. saatte tüm demoyu ekran kaydı olarak al (yedek).

## Kabul kriterleri
- [ ] `scripts/test_voice.py` bir örnek webm'i metne, bir Türkçe cümleyi mp3'e çeviriyor.
- [ ] `web.search` gerçek sonuçları başlık, URL ve snippet olarak döndürüyor.
- [ ] "Yarın 10'da toplantı" eklendikten sonra `due()` zamanı gelince kaydı bir kez döndürüyor.
- [ ] `memory.search("doğum günü")` daha önce kaydedilen ilgili notu buluyor.
- [ ] README'yi sıfırdan izleyen biri 15 dakikada sistemi ayağa kaldırabiliyor.
