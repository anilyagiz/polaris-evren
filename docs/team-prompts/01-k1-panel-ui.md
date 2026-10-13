# ROL PROMPTU — Kişi 1: Panel UI + XAI

> Önce ORTAK BAĞLAM dosyasını verdim. Şimdi senin görevin bu.

Sen bu projede **Kişi 1**'in eşli programcısısın. Benim alanım Brave extension'ın **yan panel arayüzü**: sohbet, "Neden?" zaman çizelgesi (explainable AI), onay modalı, mikrofon ve sesli cevap oynatma. WebSocket istemcisi de bende.

## Çalışma şekli
- Önce bana en fazla 10 maddelik bir uygulama planı ver, onaylayınca kodlamaya geç.
- Dosyaları **tam haliyle** ver; her dosyanın başında yolunu yaz. "… geri kalanı aynı" gibi kısaltma yapma.
- Kurulum ve çalıştırma komutlarını PowerShell uyumlu ver.
- Sözleşmeyi (protokol, TraceStep, tool adları) asla değiştirme. Belirsizlik varsa varsayımını açıkça yaz.

## Sahip olduğum dosyalar
`extension/manifest.json`, `extension/vite.config.ts`, `extension/package.json`, `extension/src/panel/**`.
**Dokunmadığım dosyalar:** `src/background.ts`, `src/content/**`, `src/bridge/**` (Kişi 2).

## Diğerleriyle arayüzüm
- Kişi 2 bana şu fonksiyonu verecek; ben sadece import edip çağıracağım:
  ```ts
  // extension/src/bridge/executeTool.ts
  export async function executeBrowserTool(
    name: string, args: Record<string, unknown>, tabId?: number
  ): Promise<{ ok: boolean; data: unknown | null; error: string | null }>;
  ```
  O hazır olana kadar `src/panel/mocks/executeTool.mock.ts` ile sahte cevap dön.
- Backend (Kişi 3) WebSocket'i `/ws`'de, Kişi 5 `/stt` ve `/tts`'i sunacak (ORTAK BAĞLAM §5–6).

## Görevler (sırayla)

### Saat 0–1: İskelet (bunu hemen push'la, Kişi 2 bekliyor)
- Vite + React + TS + `@crxjs/vite-plugin` ile MV3 extension.
- `manifest.json`:
  - `permissions`: `sidePanel`, `storage`, `scripting`, `tabs`, `activeTab`
  - `host_permissions`: `<all_urls>`, `http://localhost:8000/*`
  - `side_panel.default_path`: panel html
  - `background.service_worker`: `src/background.ts` (boş dosya bırak, Kişi 2 dolduracak)
  - `content_scripts`: `matches: ["<all_urls>"]`, `js: ["src/content/index.ts"]`, `run_at: "document_idle"` (boş dosya bırak)
  - `action` (ikon tıklanınca paneli açacak, mantığı Kişi 2 yazar)
- `npm run dev` ve `npm run build` çalışsın. Brave'de `brave://extensions` → Geliştirici modu → "Paketlenmemiş yükle" ile `dist` yüklenebilsin. Adımları README notu olarak yaz.

### Saat 1–3: Sohbet + zaman çizelgesi
- `ws.ts`: WebSocket istemcisi. Bağlanınca `hello` gönderir. Üstel bekleme ile yeniden bağlanır (1s→2s→4s, max 10s). Bağlantı durumunu (bağlı / bağlanıyor / kopuk) gösteren küçük bir gösterge.
- **Mock modu:** Ayarlarda "Mock" açıkken WebSocket yerine `shared/fixtures/session_example.jsonl` olaylarını 300 ms aralıklarla oynat. Fixture yoksa kendi küçük fixture'ını panel içinde tut.
- `user_msg` gönderirken aktif sekmenin `{id, url, title}` bilgisini `chrome.tabs.query({active:true, currentWindow:true})` ile ekle. İstek id'si `r_` + 6 rastgele karakter.
- Mesaj listesi: kullanıcı mesajları, `final.answer` markdown olarak (hafif bir markdown renderer kullan, HTML'i sanitize et), altında `citations` (url varsa link, ref varsa tıklayınca highlight).
- **"Neden?" zaman çizelgesi:** Her cevabın altında açılır-kapanır. `trace` olaylarını `request_id`'ye göre grupla. Her adımda:
  - Ajan rozeti (router/planner/executor/judge/responder için ayrı renk)
  - `decision` kalın, `why` gri metin
  - `confidence` varsa küçük çubuk
  - `tool` varsa `browser.click(ref=7)` gibi mono yazı
  - Judge adımında `scores` üç küçük puan ve `verdict` rozeti (pass yeşil, revise sarı, ask_user turuncu)
  - `evidence`: `ref:N` → tıklanabilir chip, tıklayınca `executeBrowserTool("browser.highlight", {ref:N})`; `url:` → link; `shot:` → backend adresiyle birleştirilmiş küçük görsel, tıklayınca büyür
  - `latency_ms` ve `tokens` sağda küçük
- İstek sürerken canlı olarak adımlar eklensin ve "İptal" düğmesi `cancel` göndersin.

### Saat 3–4: Tool köprüsü, onay, ses
- `tool_call` gelince `executeBrowserTool(name, args, tab.id)` çağır, sonucu `tool_result` olarak gönder. Hata olursa `ok:false, error`.
- `need_confirm` gelince modal: özet metni, tool adı ve argümanları, "Onayla" ve "Reddet". Cevap `confirm` olarak gider. 60 saniyelik geri sayım göster.
- **Mikrofon (bas-konuş):** Düğmeye basılı tut veya Space'e basılı tut. `MediaRecorder` ile `audio/webm;codecs=opus` kaydet, bırakınca `POST {backend}/stt` (multipart alan adı `audio`). Dönen metni gönderilecek mesaj olarak göster ve `source:"voice"` ile `user_msg` yolla.
  - **Bilinen sorun:** Yan panelde `getUserMedia` izin penceresi çıkmayabilir. Çözüm: `src/panel/permission.html` adında bir sayfa yap; mikrofon izni yoksa bu sayfayı yeni sekmede aç (`chrome.tabs.create`), orada `getUserMedia` çağırıp izni al, sonra sekmeyi kapat. İzin extension origin'ine verildiği için panel de kullanabilir.
- **Sesli cevap:** `final.spoken` ve `notify.spoken` için `POST {backend}/tts` → dönen mp3'ü `Audio` ile çal. İstek hata verirse `speechSynthesis` ile `tr-TR` sesle oku. Ayarlarda "Sesli yanıt" aç/kapa.
- `notify` gelince panelin üstünde kapatılabilir bir bildirim bandı göster ve sesli oku.

### Saat 4–5: Ayarlar ve cila
- Ayarlar (dişli ikonu): backend adresi, mock modu, sesli yanıt; `chrome.storage.local`'da sakla.
- Boş durum: ilk açılışta 4 örnek komut çipi (tıklayınca gönderir): "Bu sayfayı özetle", "Brave'de … ara", "Yarın 10'da hatırlat", "Bu formu doldur".
- `error` olaylarını mesaj listesinde kırmızı satır olarak göster.
- Açık/koyu tema `prefers-color-scheme` ile.

## Kabul kriterleri
- [ ] Mock modunda fixture oynatılınca sohbet, zaman çizelgesi, onay modalı ve final cevap doğru görünüyor.
- [ ] Gerçek backend'e bağlanınca `hello` gidiyor, kopunca yeniden bağlanıyor.
- [ ] `ref:` chip'ine tıklayınca sayfada öğe parlıyor (Kişi 2 hazır olduğunda).
- [ ] Bas-konuş ile kaydedilen ses metne dönüşüp gönderiliyor; cevap sesli okunuyor.
- [ ] Onay modalı reddedilince `approved:false` gidiyor.
- [ ] Panel 360 px genişlikte taşmadan çalışıyor.
