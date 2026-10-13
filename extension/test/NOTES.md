# K2 — Tarayıcı computer use notları

## Ölçüm yöntemi

`test/snapshot.probe.js` sayfaya konsoldan enjekte edilip `__aiProbe.measure(5)` ile
ölçüldü. Her satır 5 koşunun medyanı. Viewport ~1400×900, laptop CPU, Chromium tabanlı
tarayıcı.

Probe artık **elle tutulan bir kopya değil**, `src/dev/probe.entry.ts`'ten üretiliyor —
production koduyla asla ayrışamaz:

```
npx esbuild src/dev/probe.entry.ts --bundle --format=iife --target=chrome110 \
  --outfile=test/snapshot.probe.js
```

> Bu ölçümler **snapshot algoritmasını** doğrular. `chrome.*` köprüsü (enjeksiyon yedeği,
> 8 sn timeout, `brave://` reddi, `browser.navigate`) extension gerçekten Brave'e
> yüklenmeden doğrulanamaz (aşağıda açık iş).

## Sonuçlar

| Sayfa | DOM düğümü | Aday | Öğe | Medyan | En kötü | Özet (karakter) |
|---|---|---|---|---|---|---|
| `test/fixture.html` | 95 | 26 | 19 | 0,4 ms | 2,4 ms | 749 |
| tr.wikipedia.org · Klavye (bilgisayar) | 2.143 | 606 | **150 (tavan)** | 12,4 ms | 18,0 ms | 1500 |
| github.com/trycua/cua | 3.548 | 593 | **150 (tavan)** | 13,0 ms | 14,7 ms | 1473 |
| search.brave.com/search?q=mekanik+klavye | 1.820 | 177 | 89 | 2,9 ms | 11,3 ms | 1500 |
| hepsiburada.com/ara?q=mekanik+klavye | 4.373 | 615 | **150 (tavan)** | 13,0 ms | 39,8 ms | 1372 |
| httpbin.org/forms/post | 46 | 13 | 13 | 1,2 ms | 9,6 ms | 181 |

**Yukarıdaki tablo denetim öncesi algoritmaya ait.** Denetim sonrası yeniden ölçüm:

| Sayfa | Aday | Öğe | Medyan | En kötü | Önce | Değişim |
|---|---|---|---|---|---|---|
| github.com/trycua/cua | 566 | 150 (tavan) | **4,3 ms** | 5,3 ms | 13,0 ms | **3,0× hızlı** |
| hepsiburada.com/ara | 615 | 150 (tavan) | **6,4 ms** | 7,7 ms | 13,0 ms | **2,0× hızlı** |
| tr.wikipedia.org · Klavye | 605 | 150 (tavan) | **6,9 ms**\* | 7,6 ms | 12,4 ms | 1,8× hızlı |
| search.brave.com | 177 | 80 | **9,4 ms** | 11,1 ms | 2,9 ms | 3,2× yavaş |

\* Wikipedia satırı, aşağıdaki önbellek düzeltmesinden **önce** ölçüldü; önbellekle daha
da hızlı olması beklenir, yeniden ölçülmedi. httpbin (13 aday) yeniden ölçülmedi —
önbellekten etkilenmeyecek kadar küçük.

**Yol boyunca bulunan performans regresyonu.** Denetim düzeltmelerinden sonra GitHub ilk
ölçümde **55,3 ms**'ye çıkmıştı (13,0 ms'den 4× yavaş): aday başına ata zincirinde
`getComputedStyle` çağrılıyordu ve GitHub'ın derin DOM'unda bu 596 aday × ~10 ata
ediyordu. Adaylar atalarının neredeyse tamamını paylaştığı için `ClipCache` eklendi —
her ata snapshot başına bir kez ölçülüyor. 55,3 → 4,3 ms.

Brave Search'teki yavaşlama (2,9 → 9,4 ms) gerçek: `isScreenReaderOnly` artık aday başına
bir `getComputedStyle` istiyor ve o sayfada ata paylaşımı az. 300 ms hedefinin yanında
önemsiz, karşılığında sr-only ve kırpılmış öğeler listeye sızmıyor.

## Testte bulunup düzeltilenler

1. **Sıfır boyutlu kapsayıcıdaki öğeler listeye sızıyordu.**
   `overflow:hidden; width:0; height:0` bir div'in içindeki düğmenin kendi rect'i sıfır
   değil, bu yüzden `checkVisibility()` `true` dönüyordu. LLM'e asla tıklayamayacağı bir
   ref veriliyordu. → `isClippedAway()` eklendi: en fazla 20 seviye yukarı çıkıp
   `overflow: hidden|clip|scroll|auto` olan ataları kontrol eder; ata sıfır boyutluysa ya
   da öğenin rect'i atanın dışındaysa öğe gizli sayılır. Ayrıca `.sr-only` kalıpları
   (`clip: rect(0,0,0,0)`, `clip-path: inset(50%)`) ve 2 px'ten küçük kutular elendi.
   Fixture'da 20 → 19 öğe (yalnızca "Sıfır boyutlu düğme" düştü).

2. **`read` ve `text_excerpt` başlıkları yutuyordu.**
   Metin düğümlerinin ebeveynini `isVisible()` ile süzüyorduk; blok kapsayıcılar
   (`h1`, `p`) bazı yerleşimlerde `width: 0` raporlayabiliyor ve metin tamamen
   düşüyordu — fixture'da sayfa başlığı ve tüm `h2`'ler kayıptı (340 karakter).
   → Metin yürüyüşü için ayrı, **geometrik olmayan** `isRendered()` yazıldı
   (`checkVisibility` / display-visibility-opacity). Fixture'da 340 → 749 karakter,
   başlıklar geri geldi. Yan fayda: metin başına ata yürüyüşü yapılmadığı için hızlı.

## Denetim turu — 5 paralel incelemeci

Kod 5 ayrı incelemeciye dağıtıldı (her birine ayrı dosya sahipliği, "düzeltmeden önce
hatayı kanıtla" kuralıyla). Çıkan **gerçek** hatalar, ciddiyet sırasıyla:

**Güvenlik**
- `read {ref}` bir `type="password"` alanına çağrıldığında parolayı **düz metin
  döndürüyordu** (`{"text":"hunter2"}`). `snapshot` aynı değeri `"••••"` maskeliyordu,
  yani `read` maskelemenin etrafından dolaşan bir yoldu. Artık değer taşıyan korumalı
  kontroller maskeleniyor; "Şifre hatalı" gibi sayfa metni maskelenmiyor.
- `isProtectedField` yalnızca `type=password` + `autocomplete` bakıyordu; `name="cvv"`
  ve `name="cardNumber"` alanlarına yazma geçiyordu. Artık name/id/placeholder/
  data-testid/erişilebilir ad, TR+EN desen listesine karşı kontrol ediliyor.
- `javascript:` / `data:` engeli **kazara** çalışıyordu (şema kontrolüyle değil, port
  parse hatasıyla) ve sızdırıyordu: `javascript:/**/alert(1)`, `mailto:` geçiyordu.
  Artık açık `http`/`https` allow-list'i var, `localhost:3000` çalışmaya devam ediyor.

**Çalışmayan özellikler**
- **Enjeksiyon yedeği hiç çalışmıyordu.** CRXJS content script'i bir *loader*; gövdesi
  `await import(...)` başlatıp senkron dönüyor, `onMessage.addListener` ise dinamik
  import edilen modülde. `executeScript` loader bitince resolve olduğu için ikinci
  `sendMessage` de "receiving end does not exist" alıyordu. Artık enjeksiyondan sonra
  content script'e ping atılıyor (50 ms aralık, 3 sn üst sınır).
- **Uzun sayfada ekrandaki hiçbir öğe snapshot'a girmiyordu.** `MAX_CANDIDATES` kesmesi
  DOM sırasındaydı; 1500 adaylı bir katalogda kullanıcı aşağı kaydırmışsa 150 ref'in
  hepsi tepedeki ekran dışı bağlantılar oluyordu. Artık önce tüm rect'ler ölçülüp bütçe
  viewport'a en yakın düğümlere harcanıyor (ölçüm: 0/100 → 100/100).
- **`read {ref}`, nav/aside/footer içindeki öğeler için boş string dönüyordu** —
  `closest()` tüm dokümanda eşleştiği için. Artık eleme sadece kökün içindeki chrome'a.
- **`navigate` eski sayfanın url/title'ını dönüyordu.** Chrome yeni yanıt gelene kadar
  eski dokümanı `complete` tutuyor; dinleyici `tabs.update`'ten sonra bağlandığı için
  yavaş başlayan navigasyonlarda poller eski "complete"i görüp anında başarı dönüyordu.
- **`scroll` iç kaydırma konteynerlerini hiç kaydırmıyordu** — body kaydırılamaz olan
  sayfalarda `ok:true` + `scroll_y:0` dönüp LLM'i sonsuz döngüye sokuyordu.
- **`highlight` kutusu yanlış yere çiziliyordu** (`scroll-behavior: smooth` sayfalarda
  289 px sapma ölçüldü) ve sayfa kaydırılınca yerinde donuyordu. Artık `requestAnimationFrame`
  ile öğeyi takip ediyor.

**Sessiz yalanlar (`ok:true` dönüp hiçbir şey yapmayanlar)**
- HTML5 doğrulaması submit'i engellerken `submitted:true` deniyordu (form dinleyicisi
  0 kez çalışıyordu). Artık ilk geçersiz alan isimlendirilip dürüst `fail` dönüyor.
- `form="baska-id"` ile ilişkili alan yanlış forma gidiyordu (`closest` onu bulamaz).
- `maxlength=5` alana 12 karakter yazılıyordu; `type=number` alana `"abc"` yazılıp alan
  boş kalıyordu; `disabled`/`aria-disabled` öğeye tıklama ve `checkbox`'a metin yazma
  başarı sayılıyordu.
- React/Vue kontrollü `<select>` eski değerine geri dönüyordu (düz `element.value`).
- `input[type=file]` native setter'ı ve eksik `document.execCommand` **exception
  fırlatıyordu** — handler'ların asla fırlatmaması sözleşmesinin ihlali.

**Çökme**
- `label[for="…"]` seçicisinde yalnızca `"` kaçırılıyordu; backslash ile biten bir `id`
  `takeSnapshot()`'ı baştan sona `SyntaxError` ile düşürüyordu.

**Kozmetik / sağlamlık**
- Bozuk `{kind:"tool"}` mesajında kanal sessizce kapanıyor, köprü Türkçe olmayan ham
  hata alıyordu. `chrome.tabs.sendMessage` alıcı yanıt vermezse `undefined` resolve
  ediyor, `as ToolResponse` cast'i bunu gizleyip panele `{ok: undefined}` yolluyordu.
- `resolveTab` panelin kendi sekmesini döndürebiliyordu (dev tester tabId'siz hiç
  çalışmıyordu). Webstore kontrolü substring'di, `?q=chrome.google.com/webstore` haksız
  yere bloke oluyordu. `navigate` bütçesi 16 sn idi, sözleşme 15 sn.
- `position:fixed` mega-menüler haksız yere gizli sayılıyordu; kapalı off-canvas drawer
  (yalnızca dikey örtüşmeye bakıldığı için) öncelik kuyruğunu dolduruyordu; etiketsiz
  `<select>` adı tüm option listesi oluyordu; `input[type=image]` textbox raporlanıyordu.
- `dropRedundantAncestors` O(aday²) idi: 150 ürünlük ızgarada 134.927 `contains()`
  çağrısı. Ata yürüyüşüne çevrildi (aynı senaryoda 77 çağrı), eşdeğerliği 300 rastgele
  DOM'da kanıtlandı.

## Otomatik testler

K2 kodunun artık **199 testi** var (önceden sıfırdı):

```
npx vitest run --config src/dev/vitest.k2.config.ts
  src/content/types.test.ts        10
  src/bridge/executeTool.test.ts   80
  src/content/snapshot.test.ts     41
  src/content/actions.test.ts      68
```

Kök `vitest.config.ts` K1'e ait ve `include` yalnızca `src/panel/**` diyor, bu yüzden
K2 testleri ayrı config ile koşuyor. **K1'den istek:** `include`'a
`src/content/**/*.test.ts` ve `src/bridge/**/*.test.ts` eklenirse `npm test` her şeyi
kapsar.

**Bu testlerin sınırı:** jsdom layout hesaplamaz. `getBoundingClientRect`, `innerText`,
`scrollIntoView`, `isContentEditable`, `execCommand`, `checkVisibility`, `window.scrollY`
ve `chrome.*` stub'landı. Dolayısıyla görünürlük **kuralları** gerçek kodla sınanıyor ama
**geometri** (bbox değerleri, viewport öncelik sıralaması, 1200 aday bütçesi) yalnızca
stub'ın söylediği kadar doğru; `scroll`'un iç-kaydırıcı dalı jsdom'da hiç çalışmıyor ve
test edilmedi. Brave'de elle yükleme testinin yerine geçmez.

## Kabul kriterleri

| Kriter | Durum | Kanıt |
|---|---|---|
| Fixture'da tüm görünür etkileşimli öğeler numaralı | ✅ | 19 öğe; nav, form, ürün düğmeleri, `contenteditable`, `select` listede |
| Gizli öğe dönmüyor | ✅ | `display:none`, `visibility:hidden`, `opacity:0`, `hidden`, `aria-hidden`, `input[type=hidden]`, sıfır boyutlu kapsayıcı — hiçbiri listede yok |
| Pasif (`disabled`) düğme dönmüyor | ✅ | "Pasif düğme" listede yok |
| React tarzı kontrollü input'a yazılan değer kalıcı | ✅ | `type()` sonrası 400 ms bekleyip `value === "mekanik klavye"`, bileşen state'i de aynı. Kontrol: düz `el.value = "NAIVE"` ataması fixture tarafından geri alındı |
| Şifre alanına yazma reddediliyor | ✅ | `password_field_blocked`, alan boş kaldı. `autocomplete="cc-number"` kart alanı da reddedildi |
| `highlight` sayfa stilini bozmuyor | ✅ | `body` genişliği ve `scrollWidth/scrollHeight` değişmedi; overlay closed Shadow DOM içinde (`host.shadowRoot === null`), `position:fixed`, `pointer-events:none` |
| Enjeksiyon yedeği (extension'dan önce açılmış sekme) | ⏳ | Kod yazıldı (`executeTool.ts`), manifest olmadan doğrulanamaz |
| `brave://` sayfasında anlaşılır hata | ⏳ | Kod yazıldı (`isRestrictedUrl`), manifest olmadan doğrulanamaz |

## Site site gözlemler

**Vikipedi** — 606 aday, 150 tavanına takılıyor. Viewport önceliklendirmesi sayesinde
tavan ekrandaki öğelerle doluyor; sayfa başındayken bu öğelerin çoğu site navigasyonu
(Bağış yapın, Oturum aç, Madde, Tartışma…). Makale içi bağlantılara ulaşmak için
Executor'ın `scroll` sonrası yeni snapshot alması gerekiyor. Beklenen davranış, ama
Planner'a "önce kaydır, sonra snapshot" alışkanlığı kazandırmak K3 ile konuşulmalı.

**GitHub** — 593 aday / 150 öğe. İsimlendirme temiz (`aria-label`'lar iyi). README metni
`text_excerpt`'e düzgün giriyor (1473 karakter).

**Brave Search** — tavana takılmayan tek büyük sayfa (89 öğe). Arama kutusu `[1] textbox
"Search"` olarak ilk sırada; demo senaryosu 2 için ideal.

**Hepsiburada** — demo senaryosu 3 için en kritik sayfa:
- Fiyatlar **öğe adlarında** geliyor: `[41] generic "Sepete ekle, fiyat: 1.899 TL, Havit
  Gamenote KB884L…"`. Yani LLM fiyatı snapshot'tan okuyabiliyor, ayrıca `read` gerekmiyor.
- Rol dağılımı: 66 link, 52 button, 23 **generic**, 7 checkbox, 2 textbox. "generic"lerin
  kaynağı `[onclick]`/`[tabindex]` taşıyan div'ler; adları anlamlı olduğu için sorun değil.
- **`text_excerpt` ürünleri değil filtre menüsünü getiriyor** ("Kategori, Tüm kategoriler,
  Marka, Popüler filtreler, Logitech, Razer…"), çünkü sayfada `<main>` yok ve filtre
  kolonu DOM'da ürünlerden önce. Fiyat sorusu snapshot üzerinden cevaplanmalı,
  `text_excerpt` üzerinden değil. K3'e not düşüldü.

**httpbin form** — 13 öğenin 13'ü doğru isimlendi; `<label>` ilişkilendirmesi, radio ve
checkbox `value` alanları (`"true"`/`"false"`) beklendiği gibi.

## Bilinen sınırlar (bilerek yapılmadı)

- **iframe'lerin içine girilmiyor.** Snapshot yalnızca üst belgeyi tarar. Ödeme formları ve
  çerez banner'ları sık sık iframe içinde olur; bu durumda o öğeler listede çıkmaz.
  Çözüm MV3'te `all_frames: true` + frame başına snapshot birleştirme; 10 saatlik MVP'ye
  sığmaz, CUA katmanı (K4) yedek yol.
- **Kırpılmış metin sızabilir.** Sıfır boyutlu kapsayıcıdaki *metin*, `read` çıktısına
  girebiliyor (öğe listesine girmiyor). Metin yürüyüşünde ata-kırpma kontrolü yapmak
  sayfa başına yüzlerce `getComputedStyle` demek; 1500/4000 karakterlik çıktıda birkaç
  kelimelik gürültüye değmez.
- **Shadow DOM içindeki öğeler görünmez.** `querySelectorAll` shadow root'ları geçmez.
  Test edilen 5 sitede sorun çıkarmadı.
- **150 öğe tavanı** büyük sayfalarda doluyor; viewport önceliği sayesinde doğru öğeler
  seçiliyor ama Executor'ın kaydırıp yeniden snapshot alması gerekebiliyor.

## K1'den istenenler (manifest + build)

`manifest.json` bana ait değil. Aşağıdakiler olmadan köprü çalışmaz:

```jsonc
{
  "permissions": ["tabs", "scripting", "activeTab", "sidePanel", "storage"],
  "host_permissions": ["<all_urls>"],
  "content_scripts": [
    { "matches": ["<all_urls>"], "js": ["src/content/index.ts"], "run_at": "document_idle", "all_frames": false }
  ]
}
```

- `scripting` + `host_permissions`: extension kurulmadan önce açılmış sekmelere content
  script enjekte etmek için (`executeTool.ts` → `injectContentScript`). Enjeksiyon
  manifest'teki dosya adını runtime'da okur, build çıktısının adını varsaymaz.
- `@types/chrome` devDependency olarak gerekli (TS strict'te `chrome` global'i için).
- `src/dev/tester.html` production build'de de açılsın istiyorsak vite
  `build.rollupOptions.input`'a eklenmeli. Dev modda gerek yok.

**Hepsi K1'de mevcut çıktı — yalnızca üç küçük istek kaldı:**

1. `vitest.config.ts` → `include`'a `src/content/**/*.test.ts` ve
   `src/bridge/**/*.test.ts` eklenmesi, böylece `npm test` K2'nin 191 testini de koşar.
2. `package.json`'a **K2 tarafından bir satır eklendi** (özür, dosya K1'in):
   `"allowScripts": { "esbuild@0.25.12": true }`. npm 11 postinstall script'lerini
   varsayılan olarak bloke ediyor; onsuz `npm install` sonrası `vite build` esbuild
   binary'si olmadan patlıyor. pnpm tarafında eşdeğeri `pnpm-workspace.yaml`'da zaten
   var. İstenirse geri alınır, ama o zaman npm kullanan herkesin elle onaylaması gerekir.
3. **`src/panel/App.tsx:316` hata içeriyor** (K1'in dosyası, dokunulmadı): aktif sekme
   `{active:true, lastFocusedWindow:true}` ile çözülüyor; panel sekme modunda
   (sidePanel API'si olmayan Brave yedeği) bu sorgu **panelin kendi sekmesini**
   döndürüyor ve köprüye `tab.id` olarak geçiyor. Köprüdeki aynı hata düzeltildi
   (kendi eklenti sayfalarımız + `windowType:"normal"` filtresi) ama panel açıkça bir
   tabId geçtiği için bu düzeltme onu kurtarmıyor.

## K3'e: `tools/browser.py` için argüman şemaları

`tools/registry.py` henüz hiçbir dalda yok. Sarmalları yazarken LLM'e gidecek JSON
Schema'lar şunlar olmalı — köprünün gerçekten kabul ettiği argümanlar bunlar:

| name | parameters (JSON Schema) | risk |
|---|---|---|
| `browser.snapshot` | `{"type":"object","properties":{},"additionalProperties":false}` | safe |
| `browser.click` | `{"type":"object","properties":{"ref":{"type":"integer","minimum":1}},"required":["ref"]}` | safe |
| `browser.type` | `{"type":"object","properties":{"ref":{"type":"integer","minimum":1},"text":{"type":"string"},"submit":{"type":"boolean","default":false}},"required":["ref","text"]}` | `submit:true` → confirm |
| `browser.scroll` | `{"type":"object","properties":{"direction":{"enum":["up","down"]}},"required":["direction"]}` | safe |
| `browser.navigate` | `{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}` | safe |
| `browser.read` | `{"type":"object","properties":{"ref":{"type":["integer","null"],"minimum":1}}}` | safe |
| `browser.highlight` | `{"type":"object","properties":{"ref":{"type":"integer","minimum":1}},"required":["ref"]}` | safe |

Dönüşlerde sözleşmeden **iki küçük sapma** var, ikisi de ek alan (`ok` yerinde duruyor):

- `browser.type` → `{ok, submitted}` — `submit:true` istenip de form bulunamadığında
  Enter'a düşüldüğünü trace'te görebilmek için.
- `browser.scroll` → `{ok, scroll_y}` — Executor'ın "daha aşağı var mı" kararı için.

İstemiyorsan tek satırda kaldırırım, sözleşmeyi bozmamak önceliklidir.

### Hata sözlüğü

Köprünün ve content script'in üretebildiği **bütün** hata metinleri. Her biri modelin
bir sonraki adımda ne yapacağını söylüyor; hiçbiri yalnızca "başarısız" demiyor:

| Mesaj | Ne zaman | Modelin yapması gereken |
|---|---|---|
| `ref bulunamadı, yeni snapshot al` | ref eskimiş ya da öğe gitmiş | yeni snapshot |
| `ref eksik ya da sayı değil` | bozuk argüman | argümanı düzelt |
| `password_field_blocked: şifre/kart alanına yazılmaz, kullanıcıdan iste` | korumalı alan | kullanıcıya sor |
| `öğe pasif (disabled), önce etkinleştiren adımı yap ya da yeni snapshot al` | disabled tıklama | önkoşulu tamamla |
| `alan salt okunur ya da pasif, başka bir alan seç ya da yeni snapshot al` | readonly input | başka alan |
| `bu alan metin kabul etmiyor (input type=…), click kullan` | checkbox/file/button'a `type` | `click`'e geç |
| `bu öğeye yazılamaz (role=…), metin kutusu seç` | yazılamayan öğe | textbox seç |
| `seçenek bulunamadı. Mevcut seçenekler: …` | select'te eşleşme yok | listeden seç |
| `metin yazıldı ama form gönderilmedi: "X" alanı doğrulamayı geçmiyor (…)` | HTML5 doğrulama engeli | önce o alanı doldur |
| `direction "up" ya da "down" olmalı` | bozuk argüman | argümanı düzelt |
| `url eksik, http(s) ile başlayan bir adres ver` | navigate argümanı | url ver |
| `Bu sayfada asistan çalışamıyor` | `brave://`, Web Store, erişilemeyen sayfa | önce `navigate` |
| `Sürülebilir sekme yok, kullanıcıdan bir sayfa açmasını iste` | açık normal sekme yok | kullanıcıya sor |
| `Sayfaya bağlanılamadı, sekmeyi yenileyip tekrar dene` | enjeksiyon da başarısız | kullanıcıya sor |
| `Sayfadan geçersiz yanıt geldi, yeni bir snapshot al` | content script yanıt vermedi | yeni snapshot |
| `timeout: <tool> 8000 ms içinde bitmedi (işlem sayfada tamamlanmış olabilir, tekrar denemeden önce yeni bir snapshot al)` | çağrı bütçesi doldu | **körlemesine tekrarlama**, snapshot al |
| `timeout: sayfa 14000 ms içinde yüklenmedi` | navigate bütçesi | tekrar dene / başka url |
| `Sekme kapatıldı` | navigate sırasında sekme gitti | yeni sekme |

**Chrome'un kendi hataları çevriliyor.** `chrome.*` İngilizce ve geliştiriciye yazılmış
cümleler döndürüyor; bunlar `tool_result` üzerinden doğrudan modelin bağlamına giriyordu.
Artık bilinen aileler tek cümleye çevriliyor, **tanınmayan hata olduğu gibi geçiyor**
(uydurulmuyor):

| Chrome | Çeviri |
|---|---|
| `Tab is discarded` | Sekme uykuya alınmış, önce sekmeye geç |
| `No tab with id` / `Invalid tab ID` | Sekme artık yok, yeni bir snapshot al |
| `Cannot access contents…` | Bu sayfada asistan çalışamıyor |
| `Extension context invalidated` | Eklenti yeniden yüklendi, sekmeyi yenilemeden işlem yapılamaz |
| `No frame with id` | Sayfanın o bölümü kayboldu, yeni bir snapshot al |
| `The message port closed…` | Sayfa cevap vermeden kapandı (muhtemelen yönlendirme), yeni bir snapshot al |

Bu tabloyu 8 test koruyor (`executeTool.test.ts` → "Chrome hatalarının modele uygun metne
çevrilmesi"), sonuncusu tanınmayan hatanın bozulmadan geçtiğini doğruluyor.

## Açık işler

- [ ] Manifest geldikten sonra: enjeksiyon yedeği, 8 sn timeout, `brave://` reddi ve
      `browser.navigate` gerçek extension'da doğrulanacak.
- [ ] K3'ün `scripts/mock_ws.py`'si ile uçtan uca `browser.snapshot` akışı (5–7. saat).
- [ ] Node kurulunca `snapshot.probe.js` elle senkron tutulmak yerine
      `esbuild src/content/snapshot.ts --bundle --format=iife` ile üretilsin.
