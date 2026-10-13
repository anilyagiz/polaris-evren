# Demo Senaryosu — 5 Dakikalık Akış (Kişi 5)

6 senaryo, **ORTAK BAĞLAM §8** ile birebir. Her senaryoda: tam cümle, açık sayfa,
beklenen trace adımları, beklenen cevap ve *ters giderse* planı.

---

## 0. Demo öncesi kontrol listesi (5 dk önce)

- [ ] `.\scripts\run-backend.ps1 -NoReload` çalışıyor (`python scripts\doctor.py` hepsini özetler) → `http://localhost:8000/health` → `{"ok": true}`
      — **`-NoReload` (reload kapalı) olmalı**: reload her kod değişiminde whisper modelini yeniden yükler.
- [ ] `backend/.env` dolu: `DEEPSEEK_API_KEY` (evren_llm_...), `BRAVE_API_KEY`, `LLM_MODEL`, `STT_CPU_THREADS=8`
- [ ] Extension Brave'e yüklendi, panelde backend adresi `http://localhost:8000`
- [ ] Mikrofon izni verildi (`brave://settings/content/microphone`)
- [ ] Whisper modeli **bir kez önceden** `POST /stt` ile ısıtıldı (ilk istekte 4,8 sn; sonrası 2,1 sn)
- [ ] `python scripts\test_voice.py` ve `python scripts\test_tools.py` PASS
      (yalnızca `web.search` için `BRAVE_API_KEY` şart; diğer 7 kontrol anahtar olmadan da geçer)
- [ ] Ekran kaydı başlattı (yedek video için), sekme düzeni hazır:
  - Sekme A: Wikipedia makalesi (özetleme) — `https://tr.wikipedia.org/wiki/Yapay_zek%C3%A2`
  - Sekme B: e-ticaret sitesi (arama/fiyat) — `https://www.hepsiburada.com`
  - Sekme C: `brave://extensions` (panel görünür hale getirilecek)
- [ ] Yedek: cevaplar metin kutusuna elle yazılabilir; video kaydı hazır.

**Kural:** Panelin "Neden?" zaman çizelgesi her senaryoda açık tutulur — *açıklanabilirlik* vurgu.

**Sahne arkası (K5 tarafı):** 5 sn ses → `POST /stt` → 2,1 sn (warm) → cevap →
`POST /tts` → 1,2 sn (ilk) / 0,004 sn (önbellekli). Yani sesli tur başına
beklenen toplam ses gecikmesi ~3,5 sn; geri kalanı LLM ve tarayıcı adımlarıdır.

---

## Senaryo 1 — Sesli özetleme (~1 dk)

- **Açık sayfa:** Sekme A — Wikipedia "Yapay zekâ" makalesi.
- **Söylenecek cümle (bas-konuş):**
  > "Bu sayfayı özetle"
- **Beklenen trace adımları:**
  1. `router` — metni "sayfa özeti" olarak sınıflandırır.
  2. `planner` — `browser.read` (gerekirse `browser.snapshot`) planlar.
  3. `executor` — `browser.read {}` → {text ≤ 4000 karakter}; trace'te `evidence: ["url:https://tr.wikipedia.org/…"]`.
  4. `judge` — scores `{correct, complete, safe}` → `verdict: pass`.
  5. `responder` — 3-4 cümlelik özet + `spoken` düz metin.
- **Beklenen cevap:** Sayfanın ana fikirleri (2-4 madde), sesli olarak okunur.
- **Ters giderse:**
  - Ses gelmezse → paneldeki metin kutusuna aynı cümleyi yaz (kaynak `text`).
  - Özet çok uzunsa → "Kısaca" de; responder yeniden düzenler.

---

## Senaryo 2 — Web araması + karşılaştırma (~1 dk)

- **Açık sayfa:** Sekme C (panel görünür; arama arka planda).
- **Söylenecek cümle:**
  > "Brave'de en iyi 3 kablosuz kulaklığı ara, karşılaştır"
- **Beklenen trace adımları:**
  1. `router` — dış bilgi gereksinimi → arama kararı.
  2. `planner` — `web.search {query: "en iyi kablosuz kulaklık 2026", count: 5}`.
  3. `executor` — `web.search` sonucu: `{ok: true, results: [{title, url, snippet} × 5]}`.
  4. `judge` — `pass` (kaynak sayısı yeterli).
  5. `responder` — 3 ürünlü **karşılaştırma tablosu** (fiyat/özellik/kaynak) + `citations[]` dolu.
- **Beklenen cevap:** Markdown tablo; her satırda kaynak URL; `spoken`: "Üç kulaklığı karşılaştırdım, tabloyu panele bıraktım."
- **Ters giderse:**
  - `429` (limit) → "`count: 3` ile tekrar dene" de; hata mesajı panelde görünür → anlat: "Brave API limiti, kısa sürelik." (Kod 429'da 2 sn bekleyip bir kez daha dener.)
  - `BRAVE_API_KEY ayarlanmamış` → `.env` + uvicorn yeniden başlat (demo ortamında önceden doğrula).
  - Arama çalışmazsa → `memory.search` ile önceden kaydedilmiş bir notu gösterip "hafıza" özelliğini anlat (kurtarma yolu).

---

## Senaryo 3 — Sitede arama, ilk 3 fiyat (~1.5 dk) ★ zaman çizelgesi vurgusu

- **Açık sayfa:** Sekme B — `https://www.hepsiburada.com` ana sayfa.
- **Söylenecek cümle:**
  > "Bu sitede mekanik klavye ara, ilk 3 fiyatı söyle"
- **Beklenen trace adımları:**
  1. `router` → `planner`: `browser.snapshot` → `browser.type` → `browser.snapshot` → `browser.read` × 3.
  2. `executor` — `browser.snapshot {}` → 150 öğeye kadar numaralı liste: `[42] input "Ara"`, `[57] button "Ara"`.
  3. `executor` — `browser.type {ref: 42, text: "mekanik klavye", submit: true}` → **onay kapısı** devreye girerse panelde `need_confirm` (neden: submit riskli kalıp) → onayla.
  4. `executor` — yeni snapshot → ilk 3 ürünün `ref`'leri → `browser.read {ref: …}` × 3.
  5. `judge` — scores + `pass`; trace'te `evidence: ["ref:88", "ref:91", "ref:94"]`.
  6. `responder` — 3 fiyat; **panelde `ref`'lere tıklayınca highlight** yanıp söner.
- **Beklenen cevap:** "İlk üç: 1) … ₺, 2) … ₺, 3) … ₺" + zaman çizelgesinde highlight göster.
- **Ters giderse:**
  - Site tasarımı değişti / ref yok → ikinci kez `browser.snapshot` iste ("yenile" de).
  - `browser.type (submit)` onayı 60 sn içinde cevaplanmazsa **reddedilmiş sayılır** → tekrar iste ve hemen onayla.
  - Fiyat okunamazsa → `browser.read` yerine `browser.read {}` ile sayfa metnini al, fiyatları elle oku ve anlat.

---

## Senaryo 4 — Form doldurma + gönderme onayı (~1 dk) ★ güvenlik vurgusu

- **Açık sayfa:** Sekme B — herkese açık bir form sayfası
  (örn. `https://httpbin.org/forms/order` — demo için güvenli, gönderimi test eder).
- **Söylenecek cümle:**
  > "Bu formu doldur ve gönder"
- **Beklenen trace adımları:**
  1. `planner` — snapshot → alanları `browser.type (submit: false)` ile doldur → gönder.
  2. `executor` — her alan doldurulur; `trace.evidence` içinde `ref`'ler birikir.
  3. **Onay kapısı (K3):** gönderme adımında `need_confirm` frame'i:
     `summary: "Form gönderilsin mi?"`, `tool: "browser.click"`, `args: {ref: 52}`.
     Panelde onay penceresi → **"Evet"**.
  4. `judge` → `responder`: "Form gönderildi."
- **Beklenen cevap:** Gönderim özeti; **asla onaysız gönderim olmaz** (vurgu).
- **Ters giderse:**
  - Onay penceresi gelmezse → trace'te `risk` adımını göster, "göndermeden önce onay" kuralını anlat.
  - Zaman aşımı (60 sn) → "reddedildi" izlenir; tekrar gönder ve hemen onayla.
  - Şifre alanı görürse → **yazılmaz**; bunu söyleyerek durdur (bilinçli göster).

---

## Senaryo 5 — Hatırlatıcı (~1 dk)

- **Açık sayfa:** Herhangi bir sekme (panel görünür).
- **Söylenecek cümle:**
  > "Yarın saat 10'da toplantıyı hatırlat"
- **Beklenen trace adımları:**
  1. `router` → `planner`: `reminders.add {text: "…", due_iso: "YYYY-MM-DDT10:00:00+03:00"}`.
  2. `executor` — `{ok: true, id: "rem_…"}` (geçmiş zaman hatası olmaz; `TIMEZONE=Europe/Istanbul`).
  3. `judge` → `responder` — "Yarın 10:00 için hatırlatıcı kurdum."
- **Beklenen cevap:** Kurulum onayı; ayrıca paneldeki bildirim balonu görünür.
- **Canlı gösterim (isteğe bağlı):** "Bir dakika sonra da bir hatırlatıcı kur" →
  ~60 sn sonra sunucu `notify` frame'i gönderir → panelde:
  `{"type": "notify", "text": "Hatırlatma: …", "spoken": "…"}` (K3'ün 30 sn `due()` döngüsü).
- **Ters giderse:**
  - "Geçmiş bir zaman seçilemez" hatası alınırsa → güncel saati söyleyip tekrar dene.
  - notify gelmezse → `reminders.list` ile kaydı göster, 30 sn döngüsünü anlat.
  - Panelde saat kaymış görünürse → `tzdata` kurulu mu bak (`requirements.txt`'de var);
    `due_iso` yerel saat (+03:00) olarak döner, kayıt veritabanında UTC saklanır.

---

## Senaryo 6 — Masaüstü (CUA açıksa) (~1 dk)

> **Sahiplik notu:** CUA entegrasyonu ve **yedek demo videosu Kişi 4'ün** işidir
> (ORTAK BAĞLAM §9, saat 6). K5 yalnızca bu senaryoyu akışta konumlandırır.

- **Ön koşul:** `CUA_ENABLED=true`, Docker + CUA sandbox ayakta (K4).
- **Söylenecek cümle:**
  > "Masaüstünde metin editörünü aç ve Merhaba yaz"
- **Beklenen trace adımları:**
  1. `planner` — `desktop.snapshot` → `desktop.key`/`desktop.click` → `desktop.type` → `desktop.snapshot`.
  2. `executor` — her `desktop.snapshot` `shot:/shots/r_…_N.png` üretir; **zaman çizelgesinde ekran görüntüleri görünür**.
  3. `judge` → `responder` — "Metin editörüne Merhaba yazdım."
- **Beklenen cevap:** Onay + zaman çizelgesinde en az 2 ekran görüntüsü.
- **Ters giderse (CUA kapalı/kopuksa):**
  - **Video yedeği:** K4'ün önceden çektiği `demo/cua.mp4` kaydını oynat ve de:
    "Masaüstü modu Docker gerektiriyor; kayıtta gösteriyorum."
  - 6. saatte CUA kararı: çalışmıyorsa entegrasyona girme, videoya geç.
  - Video da yoksa → senaryoyu atla, kapanışa geç; 5 dakikalık akışta 15 sn'lik sapma kabul edilebilir.

---

## 5 dakikalık akış tablosu

| Süre | Ne yapılır | Vurgu |
|---|---|---|
| 0:00–0:30 | Giriş: panel açılır, zaman çizelgesi gösterilir | Explainable AI |
| 0:30–1:15 | **Senaryo 1** — sesli özet | Bas-konuş + TTS |
| 1:15–2:05 | **Senaryo 2** — arama + karşılaştırma tablosu | Kaynaklı cevap (citations) |
| 2:05–3:15 | **Senaryo 3** — sitede arama, ilk 3 fiyat | Highlight + ref kanıtları |
| 3:15–4:10 | **Senaryo 4** — form + onay penceresi | Güvenlik: onaysız gönderim yok |
| 4:10–4:45 | **Senaryo 5** — hatırlatıcı (varsa canlı notify) | Tool + background döngü |
| 4:45–5:00 | **Senaryo 6** — masaüstü (veya video yedeği) + kapanış | Computer use |

**Kapanış cümlesi:** "Her adım panelde gerekçesiyle görünüyor; riskli her aksiyon
sizin onayınızdan geçiyor; cevaplar kaynaklarıyla geliyor."

---

## Demo sonrası

- Ekran kaydını durdur, `demo/` altına yedekle (9,5. saat).
- Sorun çıkaran senaryoyu sahibine bildir (K1 panel, K2 browser, K3 onay/trace, K4 CUA, K5 ses/araç).
- Bu dosyayı çıkanlara göre güncelle (gerçek ref numaraları, gerçek cevap süreleri).
