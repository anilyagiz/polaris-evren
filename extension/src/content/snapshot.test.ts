/**
 * `takeSnapshot()` tests (K2).
 *
 * ---------------------------------------------------------------------------
 * JSDOM UYARISI — BU TESTLERİN SINIRI
 * ---------------------------------------------------------------------------
 * jsdom LAYOUT YAPMAZ. Ölçülen gerçekler (probe ile doğrulandı, jsdom 30):
 *
 *   - `getBoundingClientRect()` her öğe için {0,0,0,0} döner. `isVisible()`
 *     ilk iş olarak `rect.width/height < 2` kontrolü yaptığı için STUB OLMADAN
 *     HİÇBİR ÖĞE GÖRÜNÜR SAYILMAZ ve `elements` her zaman boş çıkar.
 *     → `Element.prototype.getBoundingClientRect` stub'lanıyor: `data-test-rect`
 *       ("x,y,w,h") varsa onu, yoksa DEFAULT_RECT (viewport içinde 100x20)
 *       döndürüyor.
 *   - `HTMLElement.prototype.innerText` jsdom'da HİÇ YOK (undefined, prototipte
 *     descriptor bile yok). `accessibleName()` adayları arasında `innerText`
 *     var; stub olmadan `<button>Gönder</button>` adı "" olur ve buton listeden
 *     düşer.
 *     → `innerText` getter'ı `textContent` döndürecek şekilde tanımlanıyor.
 *       SINIR: gerçek `innerText` gizli alt ağaçları atar ve CSS'e göre boşluk
 *       sıkıştırır; `textContent` atmaz. Yani "butonun içindeki display:none
 *       metin isme karışmasın" davranışı BURADA TEST EDİLEMİYOR.
 *   - `checkVisibility()` jsdom'da YOK. Bu yüzden testlerin çoğu `isVisible()`
 *     içindeki `getComputedStyle` YEDEK yolunu çalıştırır (gerçek Chrome'da
 *     `checkVisibility` yolu çalışır). Chromium yolunu da kapatmak için en
 *     altta ayrı bir blok `checkVisibility`'yi stub'lar.
 *
 * Yani: görünürlük KURALLARI (display/visibility/opacity/aria-hidden/hidden/
 * disabled/sr-only) gerçek kaynak koduyla test ediliyor; GEOMETRİ (hangi öğe
 * gerçekten ekranda, hangi kutu neyi kırpıyor) ise stub'ın söylediği kadar
 * doğrudur.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { takeSnapshot } from "./snapshot";
import { REF_ATTRIBUTE, elementByRef } from "./dom";
import { MAX_ELEMENTS, MAX_NAME_LENGTH } from "./types";

/** [x, y, w, h] used for any element without `data-test-rect`. */
const DEFAULT_RECT = [0, 0, 100, 20] as const;

function makeRect(x: number, y: number, w: number, h: number): DOMRect {
  return {
    x,
    y,
    width: w,
    height: h,
    top: y,
    left: x,
    right: x + w,
    bottom: y + h,
    toJSON: () => ({ x, y, width: w, height: h }),
  } as unknown as DOMRect;
}

function rectOf(element: Element): DOMRect {
  const raw = element.getAttribute("data-test-rect");
  if (raw === null) {
    return makeRect(...DEFAULT_RECT);
  }
  const parts = raw.split(",").map((part) => Number.parseFloat(part.trim()));
  return makeRect(parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 0);
}

let innerTextPatched = false;

beforeAll(() => {
  if (Object.getOwnPropertyDescriptor(HTMLElement.prototype, "innerText") === undefined) {
    Object.defineProperty(HTMLElement.prototype, "innerText", {
      configurable: true,
      get(this: HTMLElement): string {
        return this.textContent ?? "";
      },
    });
    innerTextPatched = true;
  }
});

afterAll(() => {
  if (innerTextPatched) {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).innerText;
  }
});

beforeEach(() => {
  // `restoreMocks: true` in the config undoes this after every test.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
    this: Element,
  ): DOMRect {
    return rectOf(this);
  });
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  // After head is cleared: setting .title recreates the <title> element.
  document.title = "Test sayfası";
});

describe("takeSnapshot – görünür etkileşimli öğeler", () => {
  it("görünür buton, link, input ve select'i numaralandırır", () => {
    document.body.innerHTML = `
      <a href="/urun">Ürün sayfası</a>
      <button>Sepete ekle</button>
      <input aria-label="Arama">
      <select aria-label="Beden"><option value="m">M</option></select>
      <textarea aria-label="Not"></textarea>
    `;

    const snapshot = takeSnapshot();
    const summary = snapshot.elements.map((element) => `${element.ref}:${element.role}:${element.name}`);

    expect(summary).toEqual([
      "1:link:Ürün sayfası",
      "2:button:Sepete ekle",
      "3:textbox:Arama",
      "4:combobox:Beden",
      "5:textbox:Not",
    ]);
  });

  it("zarf alanlarını sözleşmeye uygun doldurur (source/url/title/shot)", () => {
    document.body.innerHTML = `<main><p>Merhaba dünya</p></main><nav><p>Menü metni</p></nav>`;

    const snapshot = takeSnapshot();

    expect(snapshot.source).toBe("browser");
    expect(snapshot.shot).toBeNull();
    expect(snapshot.title).toBe("Test sayfası");
    expect(snapshot.url).toBe(window.location.href);
    expect(snapshot.text_excerpt).toContain("Merhaba dünya");
    // nav/footer/aside sayfa "chrome"u sayılır, metne girmez.
    expect(snapshot.text_excerpt).not.toContain("Menü metni");
  });

  it("isimsiz form alanlarını tutar, isimsiz butonu atar", () => {
    document.body.innerHTML = `
      <input>
      <button></button>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements).toHaveLength(1);
    expect(snapshot.elements[0]?.role).toBe("textbox");
  });

  it("aynı isimli sarmalayıcıyı atıp en içteki hedefi bırakır", () => {
    document.body.innerHTML = `<a href="/x"><button>Satın al</button></a>`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements).toHaveLength(1);
    expect(snapshot.elements[0]?.role).toBe("button");
  });
});

describe("takeSnapshot – gizli öğeler listeye girmez", () => {
  const hiddenCases: ReadonlyArray<readonly [string, string]> = [
    ["display:none", `<button style="display:none">Gizli</button>`],
    ["visibility:hidden", `<button style="visibility:hidden">Gizli</button>`],
    ["opacity:0", `<button style="opacity:0">Gizli</button>`],
    ["hidden attribute", `<button hidden>Gizli</button>`],
    ["aria-hidden ata", `<div aria-hidden="true"><button>Gizli</button></div>`],
    ["aria-hidden kendisi", `<button aria-hidden="true">Gizli</button>`],
    ["input[type=hidden]", `<input type="hidden" value="Gizli">`],
    ["disabled", `<button disabled>Gizli</button>`],
    ["aria-disabled", `<button aria-disabled="true">Gizli</button>`],
    ["sıfır boyut", `<button data-test-rect="0,0,0,0">Gizli</button>`],
    ["1px shim", `<button data-test-rect="0,0,1,1">Gizli</button>`],
    [
      "sr-only (clip)",
      `<button style="position:absolute;clip:rect(0,0,0,0)">Gizli</button>`,
    ],
    ["sr-only (clip-path)", `<button style="clip-path:inset(50%)">Gizli</button>`],
  ];

  for (const [label, markup] of hiddenCases) {
    it(`${label} olan öğe listede yok`, () => {
      document.body.innerHTML = `<button>Görünür</button>${markup}`;

      const snapshot = takeSnapshot();

      expect(snapshot.elements.map((element) => element.name)).toEqual(["Görünür"]);
    });
  }

  it("overflow:hidden kutusunun tamamen dışında kalan öğe listede yok", () => {
    document.body.innerHTML = `
      <button>Görünür</button>
      <div style="overflow:hidden" data-test-rect="0,0,200,50">
        <button data-test-rect="0,400,100,20">Taşan</button>
      </div>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual(["Görünür"]);
  });

  it("overflow:hidden kutusunun içinde kalan öğe listede var", () => {
    document.body.innerHTML = `
      <div style="overflow:hidden" data-test-rect="0,0,200,50">
        <button data-test-rect="0,10,100,20">İçeride</button>
      </div>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual(["İçeride"]);
  });
});

describe("takeSnapshot – ref numaralandırma", () => {
  it("ref'ler 1'den başlar ve DOM'a data-ai-ref olarak yazılır", () => {
    document.body.innerHTML = `<button>Bir</button><button>İki</button>`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.ref)).toEqual([1, 2]);
    expect(elementByRef(1)?.textContent).toBe("Bir");
    expect(elementByRef(2)?.textContent).toBe("İki");
  });

  it("her çağrıda yeniden numaralandırır: eski ref'ler silinir", () => {
    document.body.innerHTML = `<button>Bir</button><button>İki</button>`;
    takeSnapshot();

    // Araya yeni bir buton girer; eski "Bir" butonu kaybolur.
    document.body.innerHTML = `<button>Sıfır</button><button>İki</button>`;
    const second = takeSnapshot();

    expect(second.elements.map((element) => element.ref)).toEqual([1, 2]);
    expect(elementByRef(1)?.textContent).toBe("Sıfır");
    expect(elementByRef(2)?.textContent).toBe("İki");
    expect(document.querySelectorAll(`[${REF_ATTRIBUTE}]`)).toHaveLength(2);
  });

  it("bayat data-ai-ref taşıyan öğeden attribute temizlenir", () => {
    document.body.innerHTML = `<span id="bayat">metin</span><button>Bir</button>`;
    const stale = document.getElementById("bayat");
    stale?.setAttribute(REF_ATTRIBUTE, "99");

    takeSnapshot();

    expect(stale?.hasAttribute(REF_ATTRIBUTE)).toBe(false);
    // 99 artık hiçbir şeye çözülmez.
    expect(elementByRef(99)).toBeNull();
  });

  it("artık görünmeyen öğenin eski ref'i çözülmez", () => {
    document.body.innerHTML = `<button>Bir</button><button id="ikinci">İki</button>`;
    takeSnapshot();
    document.getElementById("ikinci")?.setAttribute("style", "display:none");

    takeSnapshot();

    expect(elementByRef(2)).toBeNull();
  });
});

describe("takeSnapshot – sözleşme limitleri", () => {
  it(`name ${MAX_NAME_LENGTH} karakterde kesilir`, () => {
    const long = "A".repeat(200);
    document.body.innerHTML = `<button>${long}</button>`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.name).toHaveLength(MAX_NAME_LENGTH);
    expect(snapshot.elements[0]?.name).toBe("A".repeat(MAX_NAME_LENGTH));
  });

  it(`en fazla ${MAX_ELEMENTS} öğe döner`, () => {
    document.body.innerHTML = Array.from(
      { length: MAX_ELEMENTS + 40 },
      (_unused, index) => `<button>Buton ${index}</button>`,
    ).join("");

    const snapshot = takeSnapshot();

    expect(snapshot.elements).toHaveLength(MAX_ELEMENTS);
    expect(snapshot.elements[0]?.name).toBe("Buton 0");
    expect(snapshot.elements.at(-1)?.ref).toBe(MAX_ELEMENTS);
  });

  it("input value'su da 80 karakterde kesilir", () => {
    document.body.innerHTML = `<input aria-label="Not" value="${"B".repeat(200)}">`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.value).toHaveLength(MAX_NAME_LENGTH);
  });

  it("yatayda ekran dışına kaymış (kapalı çekmece) öğeler sona atılır", () => {
    document.body.innerHTML = `
      <button data-test-rect="-320,100,300,20">Kapalı çekmecede</button>
      <button data-test-rect="10,100,100,20">Ekranda</button>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual([
      "Ekranda",
      "Kapalı çekmecede",
    ]);
  });

  it("1200 aday sınırında viewport'taki öğe DOM sırasının sonunda olsa da korunur", () => {
    const offscreen = Array.from(
      { length: 1300 },
      (_unused, index) => `<button data-test-rect="0,100000,100,20">Uzak ${index}</button>`,
    ).join("");
    document.body.innerHTML = `${offscreen}<button data-test-rect="0,10,100,20">Ekranda</button>`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.name).toBe("Ekranda");
  });

  it("viewport içindeki öğeler aşağıdakilerden önce gelir", () => {
    document.body.innerHTML = `
      <button data-test-rect="0,5000,100,20">Çok aşağıda</button>
      <button data-test-rect="0,800,100,20">Bir ekran aşağıda</button>
      <button data-test-rect="0,10,100,20">Ekranda</button>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual([
      "Ekranda",
      "Bir ekran aşağıda",
      "Çok aşağıda",
    ]);
  });
});

describe("takeSnapshot – value maskeleme", () => {
  it("şifre alanının value'su ••••", () => {
    document.body.innerHTML = `<input type="password" aria-label="Şifre" value="hunter2">`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.value).toBe("••••");
    expect(JSON.stringify(snapshot)).not.toContain("hunter2");
  });

  it("boş şifre alanı boş string döner (•••• değil)", () => {
    document.body.innerHTML = `<input type="password" aria-label="Şifre">`;

    expect(takeSnapshot().elements[0]?.value).toBe("");
  });

  it("autocomplete=cc-number ve current-password da maskelenir", () => {
    document.body.innerHTML = `
      <input autocomplete="cc-number" aria-label="Kart" value="4111111111111111">
      <input autocomplete="current-password" aria-label="Parola" value="s3cret">
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.value)).toEqual(["••••", "••••"]);
    expect(JSON.stringify(snapshot)).not.toContain("4111111111111111");
  });

  it("checkbox ve radio value'su true/false", () => {
    document.body.innerHTML = `
      <input type="checkbox" aria-label="Kabul" checked>
      <input type="checkbox" aria-label="Haber">
      <input type="radio" aria-label="Kargo" checked>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.value)).toEqual(["true", "false", "true"]);
  });

  it("select value'su seçili option'ın metni, link'in value'su null", () => {
    document.body.innerHTML = `
      <select aria-label="Beden">
        <option value="s">Small</option>
        <option value="l" selected>Large</option>
      </select>
      <a href="/x">Link</a>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.value).toBe("Large");
    expect(snapshot.elements[1]?.value).toBeNull();
  });
});

describe("takeSnapshot – bbox", () => {
  it("bbox [x, y, w, h] sırasındadır ve yuvarlanır", () => {
    document.body.innerHTML = `<button data-test-rect="12.4,34.6,100.5,20.2">Kutu</button>`;

    const snapshot = takeSnapshot();

    expect(snapshot.elements[0]?.bbox).toEqual([12, 35, 101, 20]);
  });

  it("negatif (yukarı kaymış) koordinatlar korunur", () => {
    document.body.innerHTML = `<button data-test-rect="-5,-10,100,20">Kutu</button>`;

    expect(takeSnapshot().elements[0]?.bbox).toEqual([-5, -10, 100, 20]);
  });
});

describe("takeSnapshot – isim çözümlemesi", () => {
  it("aria-label, aria-labelledby, label[for] ve placeholder sırasıyla kullanılır", () => {
    document.body.innerHTML = `
      <input aria-label="Birinci">
      <span id="etiket">İkinci</span><input aria-labelledby="etiket">
      <label for="ucuncu">Üçüncü</label><input id="ucuncu">
      <input placeholder="Dördüncü">
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual([
      "Birinci",
      "İkinci",
      "Üçüncü",
      "Dördüncü",
    ]);
  });

  it("isimdeki boşluklar sıkıştırılır", () => {
    document.body.innerHTML = `<button>  Sepete\n\t  ekle  </button>`;

    expect(takeSnapshot().elements[0]?.name).toBe("Sepete ekle");
  });

  it("explicit role attribute tag'i ezer", () => {
    document.body.innerHTML = `<div role="button" tabindex="0">Özel buton</div>`;

    expect(takeSnapshot().elements[0]?.role).toBe("button");
  });
});

/**
 * Gerçek Chrome'da `isVisible()` `checkVisibility()` dalından döner; jsdom'da
 * bu API olmadığı için yukarıdaki testler yedek (`getComputedStyle`) dalını
 * çalıştırıyor. Burada API'yi stub'layıp Chromium dalını da kapsıyoruz.
 */
describe("takeSnapshot – checkVisibility dalı (stub'lı)", () => {
  beforeEach(() => {
    Object.defineProperty(Element.prototype, "checkVisibility", {
      configurable: true,
      writable: true,
      value(this: Element): boolean {
        return this.getAttribute("data-test-visible") !== "false";
      },
    });
  });

  afterAll(() => {
    delete (Element.prototype as unknown as Record<string, unknown>).checkVisibility;
  });

  it("checkVisibility false diyen öğe listeye girmez", () => {
    document.body.innerHTML = `
      <button>Görünür</button>
      <button data-test-visible="false">Gizli</button>
    `;

    const snapshot = takeSnapshot();

    expect(snapshot.elements.map((element) => element.name)).toEqual(["Görünür"]);
  });

  it("checkVisibility varken geometri kontrolü yine uygulanır", () => {
    document.body.innerHTML = `<button data-test-rect="0,0,0,0">Sıfır kutu</button>`;

    expect(takeSnapshot().elements).toHaveLength(0);
  });
});
