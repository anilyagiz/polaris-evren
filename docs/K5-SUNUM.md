# Kişi 5 — Sunum Notları (ses, araçlar, dokümantasyon)

Bu dosya, sunumda **K5 kısmını** anlatmak için hazırlandı. Sırayla okunabilir;
her bölümün başında "kaç saniyede anlatılır" yazıyor.

---

## 1. Görevim neydi? (30 sn)

> "Ben asistanın **ses ve araç** katmanından sorumluydum. Üç işim vardı:
> **birincisi** kullanıcının sesini yazıya çevirmek — çünkü Brave tarayıcı
> içindeki `webkitSpeechRecognition`'ı desteklemiyor, sesi backend'e gönderip
> orada çözmemiz gerekiyordu. **İkincisi** asistanın cevabını mp3 olarak sesli
> okumak. **Üçüncüsü** asistanın dünyaya dokunduğu araçları yazmak: web araması,
> kalıcı hafıza ve hatırlatıcılar. Ayrıca kurulum README'sini ve demo senaryosunu
> ben hazırladım."

**Tek cümlelik özet:** *"Sesin asistanla buluştuğu yer ve asistanın dünyaya
dokunduğu araçlar."*

---

## 2. Ne teslim ettim? (45 sn)

| Dosya | Ne yapıyor |
|---|---|
| `backend/voice/stt.py` | `POST /stt` — bas-konuş kaydını (webm/opus) Türkçe metne çevirir. faster-whisper, CPU, int8. |
| `backend/voice/tts.py` | `POST /tts` — cevabı mp3'e çevirir. edge-tts, önbellekli. Hata olursa 503 döner, panel tarayıcı sesine geçer. |
| `backend/tools/search.py` | `web.search` — Brave Search API ile kaynaklı arama. |
| `backend/tools/memory.py` | `memory.save` / `memory.search` — kalıcı not hafızası + `relevant()` (planner'a bağlam sağlar). |
| `backend/tools/reminders.py` | `reminders.add` / `reminders.list` + `due()` — zamanı gelince orchestrator'a bildirir. |
| `backend/db.py` | Tek SQLite dosyası (`backend/data/assistant.db`), `.env` yükleyici, bağlantı yönetimi. |
| `scripts/test_voice.py`, `scripts/test_tools.py` | Kendi modüllerimin tek başına çalıştığını kanıtlayan test scriptleri. |
| `docs/README.md` | Sıfırdan 15 dakikada kurulum kılavuzu. |
| `demo/script.md` | 5 dakikalık demo akışı, 6 senaryo, her biri için başarısızlık planı. |

---

## 3. Ölçülebilir sonuçlar (45 sn) — **sunumda göster**

Hepsi Ryzen 7 4800H (8 fiziksel çekirdek) üzerinde ölçüldü, 5,8 sn'lik gerçek sesle.

| Adım | Sözleşmedeki hedef | Ölçülen | Durum |
|---|---|---|---|
| STT `small` — ısınmış (model bellekte) | < 3,0 sn | **2,14 / 2,42 sn** | ✅ |
| STT `small` — ilk çağrı (model yüklenir) | — | 4,80–5,23 sn | tek seferlik |
| STT `base` — ısınmış | < 3,0 sn | 0,93 sn | yedek |
| TTS — ilk çağrı (ağ) | < 1,5 sn | **1,19 sn** | ✅ |
| TTS — önbellekten | < 0,2 sn | **0,004 sn** | ✅ |

> "Yani bas-konuş ile sesli cevap arasındaki ses gecikmesi yaklaşık **3,5 saniye**.
> Geri kalan süre LLM ve tarayıcı adımları."

---

## 4. Sunumda öne çıkarılacak 4 mühendislik kararı (2 dk)

Bunlar "biz sadece kod yazmadık, ölçtük ve karar verdik" mesajını verir.

### ① 16 çekirdek vermek daha yavaştı
faster-whisper'ı CTranslate2 çalıştırıyor. Başlangıçta hiçbir thread ayarı
yoktu; kütüphane 16 **mantıksal** çekirdeği görüp aşırı abone oluyordu
(oversubscription) ve yavaşlıyordu. **Fiziksel çekirdek sayısını (8)** verdik:
`2,90 sn → ~2,20 sn`. Hedefe olan marj %3'ten %27'ye çıktı — yani demo günü
arka planda Chrome ve ekran kaydı çalışırken de hedefi tutturuyor.

### ② Çalışan bir şeyi bozan "düzeltme"yi kaldırdık
Kodda edge-tts'in uç noktasını **koşulsuz** değiştiren bir yama vardı. Ölçtük:
varsayılan uç nokta zaten sorunsuz çalışıyor (**1,19 sn**). Yani yama gereksizdi
ve her TTS çağrısında devredeydi — Microsoft tarafında bir değişiklik olsa tüm
sesli cevap sessizce ölürdü. **"Önce normal dene, başarısız olursa yama ile
tekrar dene"** hâline getirdik.

### ③ Saat dilimi hatası: hatırlatıcı 3 saat kayabilirdi
`_tz()` fonksiyonu log'da "+03:00 kullanılıyor" yazıyordu ama gerçekte **UTC**
döndürüyordu. `tzdata` paketi eksik olsaydı kullanıcı "yarın 10'da" der,
hatırlatıcı **07:00'de** gelirdi. Sabit +03:00'a çevirdik ve uyarı log'u ekledik.

### ④ Eski veritabanı hatırlatıcıları sonsuza kadar tekrarlatacaktı
Bu en kritik olanı. İlk sürümde tabloların `id` kolonu **metin** tipindeydi;
yeni sürümde tam sayı. SQLite'ta `CREATE TABLE IF NOT EXISTS` mevcut tabloya
dokunmaz — yani yeni kod eski tabloya `NULL` id yazar, sonra
`UPDATE ... WHERE id = ?` **hiçbir satırı eşleştiremez**. Sonuç: hatırlatıcı
30 saniyede bir, sonsuza kadar tekrar tekrar bildirilirdi.
**Otomatik şema göçü yazdık** ve gerçek veritabanının kopyası üzerinde
kanıtladık: `due()` ilk çağrıda 1 kayıt, ikinci çağrıda 0 kayıt.

> Ek olarak: ekip arkadaşımız K5 işini bağımsız olarak da yapmıştı. İki sürümü
> karşılaştırıp **iyi olan parçaları birleştirdik** — onun Türkçe kelime
> normalizasyonu ve atomik işlem yaklaşımını aldık, bizim ayarlarımızı ve
> dokümantasyonumuzu koruduk.

---

## 5. Demo'daki rolüm (30 sn)

Sunum sırasında **benim modüllerim şu anlarda konuşuyor:**

| Senaryo | Benim kısmım |
|---|---|
| 1 — "Bu sayfayı özetle" | **Bas-konuş → `/stt` → cevap → `/tts`** — sesli turun tamamı |
| 2 — "En iyi 3 kablosuz kulaklığı ara" | **`web.search`** → Brave'den kaynaklı sonuçlar |
| 5 — "Yarın saat 10'da toplantıyı hatırlat" | **`reminders.add`** + 30 sn'lik **`due()`** döngüsü → `notify` |
| (arka planda) | **`memory`** — kullanıcı tercihlerini hatırlar |

Zaman çizelgesinde ayrıca benim modüllerimin ürettiği `evidence` kayıtları
(`url:...`) görünür — açıklanabilirlik kısmına katkım.

---

## 6. Kabul kriterleri — dürüst durum

| Kriter | Durum |
|---|---|
| `test_voice.py` örnek webm'i metne, Türkçe cümleyi mp3'e çeviriyor | ✅ PASS |
| `web.search` gerçek sonuçları başlık/URL/özet olarak döndürüyor | ⏳ kod hazır, `BRAVE_API_KEY` girilmesi bekliyor |
| "Yarın 10'da toplantı" sonrası `due()` kaydı **bir kez** döndürüyor | ✅ PASS |
| `memory.search("doğum günü")` kaydedilen notu buluyor | ✅ PASS |
| README'yi izleyen biri 15 dakikada sistemi kuruyor | ✅ (adımlar tek tek test edildi) |

**Sunumdan önce yapılacak tek şey:** `backend/.env` içine `BRAVE_API_KEY`
yazıp `python scripts\test_tools.py` çalıştırmak → 13/13 PASS.
(Bu anahtar olmadan 2 kontrol dürüstçe FAIL veriyor; test bunu gizlemiyor.)

---

## 7. Sorulabilecek sorular ve cevapları

**"Neden `small` model, `base` daha hızlı değil mi?"**
> Base gerçekten ~2 kat hızlı (0,93 sn vs 2,2 sn). Ama bas-konuş kısa komutlarla
> çalışıyoruz ve Türkçe'de base ek/kök hataları yapıyor — "toplantı" yerine
> yanlış bir kelime duyulursa tüm planlayıcı bozulur. 1 saniyelik kazanç için
> doğruluktan vazgeçmedik. Sözleşme de `small` diyor; `base` `.env`'den tek
> satırla açılabilir.

**"Ses neden tarayıcıda değil backend'de çözülüyor?"**
> Brave, `webkitSpeechRecognition` API'sini desteklemiyor — bu Chrome'a özel.
> Bu yüzden sesi webm/opus olarak backend'e gönderiyoruz. Ayrıca böylece model
> tek bir yerde duruyor ve tarayıcı sürümünden bağımsız oluyoruz.

**"TTS internet istiyorsa demo sırasında koparsa ne olur?"**
> İki katmanlı koruma var: önce yedek uç nokta denenir, olmazsa `/tts` 503
> döner ve panel tarayıcının kendi sesine geçer. Yani asistan susmaz, sadece
> sesi değişir. Ayrıca mp3'ler önbellekli, demo sırasında cevaplar önceden
> ısıtılıyor.

**"Hafıza aramasında Türkçe'ye özel ne yaptınız?"**
> SQLite'ın `lower()` fonksiyonu Türkçe İ/ı ayrımını bilmiyor. Bu yüzden metni
> **kayıt anında** Türkçe kurallarına göre normalize edip ayrı bir kolona
> yazıyoruz (`text_norm`). Ayrıca "ve, ile, bir" gibi kelimeleri ayıklıyoruz —
> yoksa her not her sorguyla eşleşir ve sıralama anlamsızlaşır.

**"Neden gTTS gibi ikinci bir ses motoru yok?"**
> Denendi, ama `gTTS` `click<8.2` istiyor, `huggingface-hub` (whisper'ın model
> indiricisi) `click>=8.4.2` istiyor — ikisi aynı ortamda çalışmıyor ve model
> indirmesi bozuluyor. Sözleşmenin öngördüğü yedek yol zaten panelin tarayıcı
> sesi; gereksiz bir bağımlılık riski almadık.

**"Kod neden İngilizce?"**
> Ekip kuralı: kod ve tanımlayıcılar İngilizce, kullanıcıya görünen metinler
> Türkçe. Hata mesajları, tool açıklamaları ve tüm dokümantasyon Türkçe —
> LLM'e giden tool açıklamaları dahil.

---

## 8. Kapanış cümlesi (10 sn)

> "Özetle: asistanın **kulağı** ve **dünyaya uzanan elleri** bendeydi. Ölçtüm,
> darboğazı buldum ve düzelttim; sesli turu 3,5 saniyeye indirdim ve tüm
> senaryoları çalıştıran test scriptleriyle bıraktım."
