# K2 — Tarayıcı computer use notları

## Ölçüm yöntemi

`test/snapshot.probe.js` (production `src/content/{dom,snapshot}.ts` kodunun birebir JS
aynası) sayfaya konsoldan enjekte edilip `__aiProbe.measure(5)` ile ölçüldü. Her satır
5 koşunun medyanı. Viewport ~1400×900, laptop CPU, Chromium tabanlı tarayıcı.

> Bu ölçümler **snapshot algoritmasını** doğrular. `chrome.*` köprüsü (enjeksiyon yedeği,
> 8 sn timeout, `brave://` reddi, `browser.navigate`) extension gerçekten yüklenmeden
> doğrulanamaz — K1 manifest'i push'ladıktan sonra tekrar koşulacak (aşağıda açık iş).

## Sonuçlar

| Sayfa | DOM düğümü | Aday | Öğe | Medyan | En kötü | Özet (karakter) |
|---|---|---|---|---|---|---|
| `test/fixture.html` | 95 | 26 | 19 | 0,4 ms | 2,4 ms | 749 |
| tr.wikipedia.org · Klavye (bilgisayar) | 2.143 | 606 | **150 (tavan)** | 12,4 ms | 18,0 ms | 1500 |
| github.com/trycua/cua | 3.548 | 593 | **150 (tavan)** | 13,0 ms | 14,7 ms | 1473 |
| search.brave.com/search?q=mekanik+klavye | 1.820 | 177 | 89 | 2,9 ms | 11,3 ms | 1500 |
| hepsiburada.com/ara?q=mekanik+klavye | 4.373 | 615 | **150 (tavan)** | 13,0 ms | 39,8 ms | 1372 |
| httpbin.org/forms/post | 46 | 13 | 13 | 1,2 ms | 9,6 ms | 181 |

**Hedef < 300 ms'in çok altında.** En kötü durum 39,8 ms (hepsiburada, ilk koşu — sonraki
koşular 13 ms; fark layout cache'inin ısınması). İsimsiz öğe sayısı her sayfada 0.

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

## Açık işler

- [ ] Manifest geldikten sonra: enjeksiyon yedeği, 8 sn timeout, `brave://` reddi ve
      `browser.navigate` gerçek extension'da doğrulanacak.
- [ ] K3'ün `scripts/mock_ws.py`'si ile uçtan uca `browser.snapshot` akışı (5–7. saat).
- [ ] Node kurulunca `snapshot.probe.js` elle senkron tutulmak yerine
      `esbuild src/content/snapshot.ts --bundle --format=iife` ile üretilsin.
