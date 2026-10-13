# ROL PROMPTU — Kişi 2: Tarayıcı Computer Use (DOM)

> Önce ORTAK BAĞLAM dosyasını verdim. Şimdi senin görevin bu.

Sen bu projede **Kişi 2**'nin eşli programcısısın. Benim alanım asistanın **tarayıcıyı kullanması**: sayfayı LLM'in okuyabileceği numaralı öğe listesine çevirmek (snapshot) ve `click / type / scroll / navigate / read / highlight` aksiyonlarını güvenli şekilde uygulamak.

## Çalışma şekli
- Önce en fazla 10 maddelik plan ver, onaylayınca kodla.
- Dosyaları **tam haliyle** ver, başında yolunu yaz. Kısaltma yapma.
- Harici kütüphane ekleme (saf TypeScript + Chrome API). Gerekirse gerekçesini yaz.
- Sözleşmeyi (tool adları, argümanlar, Snapshot formatı) değiştirme.

## Sahip olduğum dosyalar
`extension/src/background.ts`, `extension/src/content/**`, `extension/src/bridge/executeTool.ts`, `extension/src/dev/**`, `extension/test/**`.
**Dokunmadığım dosyalar:** `manifest.json`, `vite.config.ts`, `src/panel/**` (Kişi 1). Manifest'e ek izin gerekirse Kişi 1'e söyleyeceğim.

## Sağlamam gereken arayüz
Panel (Kişi 1) sadece bunu çağıracak:
```ts
// extension/src/bridge/executeTool.ts
export async function executeBrowserTool(
  name: string,                       // "browser.snapshot" | "browser.click" | ...
  args: Record<string, unknown>,
  tabId?: number                      // yoksa aktif sekme
): Promise<{ ok: boolean; data: unknown | null; error: string | null }>;
```
Saat 1'de bu fonksiyonun **imzasını ve sahte bir uygulamasını** push'la ki Kişi 1 import edebilsin. Sonra gerçeğini yaz.

## Görevler (sırayla)

### Saat 1–2: Köprü ve background
- `background.ts`: `chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })`. Brave'de side panel açılmazsa yedek olarak `action` tıklanınca panel sayfasını yeni sekmede aç.
- `executeTool.ts`:
  - `tabId` yoksa aktif sekmeyi bul.
  - `browser.navigate` → `chrome.tabs.update(tabId, {url})`, `status === "complete"` olana kadar bekle (max 15 sn), `{ok, url, title}` dön.
  - Diğerleri → `chrome.tabs.sendMessage(tabId, {kind: "tool", name, args})`.
  - "Receiving end does not exist" hatası gelirse (sayfa extension kurulmadan önce açılmış) `chrome.scripting.executeScript` ile content script'i enjekte et ve bir kez tekrar dene.
  - `chrome://`, `brave://`, `chrome-extension://` ve Web Store sayfalarında `{ok:false, error:"Bu sayfada asistan çalışamıyor"}` dön.
  - Her çağrı en fazla 8 saniye sürsün, aşarsa `timeout` hatası.

### Saat 2–4: Content script
`src/content/index.ts` mesajı dinler ve doğru modüle yönlendirir.

**`snapshot.ts`**
- Aday seçici: `a[href], button, input:not([type=hidden]), textarea, select, summary, [role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=combobox], [role=textbox], [contenteditable=true], [onclick], [tabindex]:not([tabindex="-1"])`.
- Görünürlük: genişlik ve yükseklik > 0, `visibility`/`display` gizli değil, `opacity` > 0, `aria-hidden` değil. Viewport içindekileri önceliklendir, sonra viewport'un 1 ekran altındakiler.
- Erişilebilir ad sırası: `aria-label` → `aria-labelledby` → bağlı `<label>` → `placeholder` → `innerText` → `title` → `alt` → `value`. Boşlukları sadeleştir, 80 karaktere kes.
- Rol: açık `role` → etiket tabanlı (`a`→link, `button`→button, `input[type=checkbox]`→checkbox, `input`/`textarea`→textbox, `select`→combobox).
- Her snapshot'ta önceki `data-ai-ref` niteliklerini temizle, yeni öğelere 1'den başlayarak `data-ai-ref` ver.
- En fazla 150 öğe. `value`: input değerleri (password ise `"••••"`).
- `text_excerpt`: `main`, `article`, `[role=main]` veya `body`'nin görünür metninden ilk 1500 karakter.
- Dönüş: ORTAK BAĞLAM §7 Snapshot formatı, `source: "browser"`, `bbox` viewport koordinatlarıyla.
- Hedef süre: tipik sayfada < 300 ms. iframe'lerin içine girme (not olarak bırak).

**`actions.ts`**
- `click {ref}`: öğeyi bul (`[data-ai-ref="N"]`), yoksa `"ref bulunamadı, yeni snapshot al"` hatası. `scrollIntoView({block:"center"})`, `focus()`, `click()`. Sonra DOM'un durulmasını bekle: `MutationObserver` 300 ms sessiz kalana kadar, en fazla 1,5 sn.
- `type {ref, text, submit?}`:
  - `input[type=password]` veya `autocomplete` içinde `password`/`cc-` geçen alanlara **yazma**, `"password_field_blocked"` hatası dön.
  - React/Vue uyumlu yazım: native value setter (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set`) + `input` ve `change` olayları. `contenteditable` için `document.execCommand("insertText")`.
  - `submit:true` → form varsa `form.requestSubmit()`, yoksa Enter `keydown/keypress/keyup`.
- `scroll {direction}`: `window.scrollBy` ile viewport'un %80'i kadar.
- `read {ref?}`: ref varsa öğenin `innerText`'i; yoksa ana içerik (nav/footer/aside/script/style hariç) metni. 4000 karaktere kes.
- `highlight {ref}`: öğenin üstüne `position:fixed` bir kutu çiz (turuncu kenarlık, yumuşak gölge), 1,5 sn içinde iki kez yanıp sönsün ve kaybolsun. Sayfa stillerinden etkilenmemesi için Shadow DOM içinde çiz. Öğeyi önce görünür alana kaydır.

### Saat 4–5: Test sayfası ve araç
- `extension/test/fixture.html`: arama kutusu, ürün listesi (fiyatlı), giriş formu (ad, e-posta, şifre), "Gönder" ve "Sil" düğmeleri, gizli öğe, `contenteditable` alan, React tarzı kontrollü input taklidi.
- `extension/src/dev/tester.html`: aktif sekmeye her tool'u elle çağırıp JSON sonucunu gösteren basit sayfa (geliştirme için).
- 5 gerçek sitede dene (örn. Brave Search, Wikipedia, GitHub, bir e-ticaret sitesi, bir form sayfası) ve sonuçları `extension/test/NOTES.md`'ye yaz: öğe sayısı, süre, sorunlar.

### Saat 5–7: Entegrasyon
- Kişi 3'ün `scripts/mock_ws.py`'si gerçek `browser.snapshot` çağrısı gönderiyor; panel üzerinden uçtan uca çalıştığını doğrula.
- Executor'un sık yaptığı hataları (yanlış ref, eski snapshot) Kişi 3'le birlikte gözden geçir; hata mesajlarını LLM'in anlayacağı netlikte yaz.

## Kabul kriterleri
- [ ] `executeBrowserTool("browser.snapshot", {})` fixture sayfasında tüm görünür etkileşimli öğeleri numaralı döndürüyor, gizli öğeyi döndürmüyor.
- [ ] Kontrollü (React tarzı) input'a yazılan değer kalıcı.
- [ ] Şifre alanına yazma girişimi reddediliyor.
- [ ] `highlight` sayfanın stilini bozmuyor.
- [ ] Extension yüklenmeden önce açılmış sekmede de çalışıyor (enjeksiyon yedeği).
- [ ] `brave://` sayfasında anlaşılır hata dönüyor.
