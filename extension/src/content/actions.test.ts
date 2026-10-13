/**
 * `actions.ts` tests (K2): click / type / scroll / read.
 *
 * ---------------------------------------------------------------------------
 * JSDOM UYARISI — STUB'LANMAK ZORUNDA OLANLAR
 * ---------------------------------------------------------------------------
 *   - `Element.prototype.scrollIntoView` jsdom'da YOK (undefined). Hem `click`
 *     hem `type` ilk iş olarak `scrollIntoView()` çağırır; stub olmadan her
 *     çağrı TypeError ile patlar. → no-op spy.
 *   - `document.execCommand` jsdom'da YOK. `typeIntoContentEditable()` metni
 *     `execCommand("insertText", ...)` ile yazar. → Stub çağrıyı kaydediyor ve
 *     seçili aralığa metni elle yazıyor.
 *     SINIR: contenteditable testi "kaynak doğru execCommand çağrısını yapıyor
 *     mu" sorusunu yanıtlar; tarayıcının insertText'i gerçekten uyguladığını
 *     DOĞRULAMAZ.
 *   - `window.scrollBy` jsdom'da gerçek kaydırma yapmaz ve `window.scrollY`
 *     her zaman 0'dır. → `scrollBy` spy'lanıp argümanı doğrulanıyor; dönen
 *     `scroll_y` değerinin anlamı jsdom'da yok.
 *   - `HTMLElement.prototype.innerText` jsdom'da YOK; `roleOf`/hata mesajları
 *     için gerekmiyor ama `read()` yolundaki etiket çözümlemesi için
 *     tanımlanıyor (bkz. snapshot.test.ts başlığı).
 *   - `HTMLElement.prototype.isContentEditable` jsdom'da HER ZAMAN false
 *     (contenteditable hiç desteklenmiyor). Stub olmadan `type()` her
 *     contenteditable öğeyi "bu öğeye yazılamaz" diye reddeder.
 *     → `contenteditable` attribute'una bakan bir getter tanımlanıyor.
 *
 * Geri kalan her şey gerçek: native value setter, InputEvent/Event akışı,
 * `form.requestSubmit()` (jsdom gerçekten submit olayı fırlatıyor), ref
 * çözümlemesi, korumalı alan reddi.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as actions from "./actions";
import { REF_ATTRIBUTE } from "./dom";

let innerTextPatched = false;
let execCommandPatched = false;
let originalContentEditable: PropertyDescriptor | undefined;

/** jsdom ignores `contenteditable`; derive the flag from the attribute. */
function isContentEditableFromAttribute(element: HTMLElement): boolean {
  const own = element.getAttribute("contenteditable");
  if (own === "false") {
    return false;
  }
  if (own === "true" || own === "") {
    return true;
  }
  return element.closest('[contenteditable="true"], [contenteditable=""]') !== null;
}

/** Minimal insertText: replace the current selection's contents with `text`. */
function fakeExecCommand(command: string, _showUi?: boolean, value?: string): boolean {
  if (command !== "insertText") {
    return false;
  }
  const range = window.getSelection()?.getRangeAt(0);
  if (range === undefined) {
    return false;
  }
  range.deleteContents();
  range.insertNode(document.createTextNode(value ?? ""));
  return true;
}

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
  originalContentEditable = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "isContentEditable",
  );
  Object.defineProperty(HTMLElement.prototype, "isContentEditable", {
    configurable: true,
    get(this: HTMLElement): boolean {
      return isContentEditableFromAttribute(this);
    },
  });
  if ((document as unknown as { execCommand?: unknown }).execCommand === undefined) {
    (document as unknown as { execCommand: typeof fakeExecCommand }).execCommand =
      fakeExecCommand;
    execCommandPatched = true;
  }
});

afterAll(() => {
  if (innerTextPatched) {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).innerText;
  }
  if (execCommandPatched) {
    delete (document as unknown as Record<string, unknown>).execCommand;
  }
  if (originalContentEditable === undefined) {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).isContentEditable;
  } else {
    Object.defineProperty(HTMLElement.prototype, "isContentEditable", originalContentEditable);
  }
});

beforeEach(() => {
  document.body.innerHTML = "";
  // jsdom has no scrollIntoView at all; `restoreMocks` removes this afterwards.
  (Element.prototype as unknown as Record<string, unknown>).scrollIntoView = () => undefined;
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => undefined);
});

/** Build markup and hand ref numbers out in document order, like a snapshot. */
function mount(markup: string): void {
  document.body.innerHTML = markup;
  const targets = document.querySelectorAll<HTMLElement>("[data-ref]");
  for (const target of targets) {
    target.setAttribute(REF_ATTRIBUTE, target.getAttribute("data-ref") ?? "");
  }
}

function recordEvents(element: Element, types: readonly string[]): string[] {
  const seen: string[] = [];
  for (const type of types) {
    element.addEventListener(type, () => seen.push(type));
  }
  return seen;
}

describe("type – native value setter yolu", () => {
  it("input'a yazar, değer kalıcı olur ve input/change olayları tetiklenir", async () => {
    mount(`<input data-ref="1" aria-label="Arama">`);
    const input = document.querySelector("input") as HTMLInputElement;
    const seen = recordEvents(input, ["input", "change"]);

    const result = await actions.type({ ref: 1, text: "polaris" });

    expect(result).toEqual({ ok: true, data: { ok: true, submitted: false }, error: null });
    expect(input.value).toBe("polaris");
    expect(seen).toEqual(["input", "change"]);
  });

  it("prototip setter'ı kullanır: framework tracker'ı gören InputEvent gider", async () => {
    mount(`<input data-ref="1">`);
    const input = document.querySelector("input") as HTMLInputElement;
    const events: Event[] = [];
    input.addEventListener("input", (event) => events.push(event));

    await actions.type({ ref: 1, text: "merhaba" });

    expect(events).toHaveLength(1);
    expect(events[0]).toBeInstanceOf(InputEvent);
    expect((events[0] as InputEvent).data).toBe("merhaba");
    expect((events[0] as InputEvent).inputType).toBe("insertText");
    expect(events[0]?.bubbles).toBe(true);
  });

  it("textarea'ya da aynı yolla yazar", async () => {
    mount(`<textarea data-ref="7"></textarea>`);
    const area = document.querySelector("textarea") as HTMLTextAreaElement;

    const result = await actions.type({ ref: 7, text: "uzun metin" });

    expect(result.ok).toBe(true);
    expect(area.value).toBe("uzun metin");
  });

  it("önceki değeri değiştirir, eklemez", async () => {
    mount(`<input data-ref="1" value="eski">`);
    const input = document.querySelector("input") as HTMLInputElement;

    await actions.type({ ref: 1, text: "yeni" });

    expect(input.value).toBe("yeni");
  });

  it("boş metin yazmak değeri temizler", async () => {
    mount(`<input data-ref="1" value="eski">`);
    const input = document.querySelector("input") as HTMLInputElement;

    const result = await actions.type({ ref: 1, text: "" });

    expect(result.ok).toBe(true);
    expect(input.value).toBe("");
  });

  it("salt okunur ya da pasif alanı reddeder", async () => {
    mount(`<input data-ref="1" value="sabit" readonly><input data-ref="2" value="pasif" disabled>`);
    const [readonlyInput, disabledInput] = Array.from(document.querySelectorAll("input"));

    const readonlyResult = await actions.type({ ref: 1, text: "x" });
    const disabledResult = await actions.type({ ref: 2, text: "x" });

    expect(readonlyResult).toMatchObject({ ok: false, data: null });
    expect(readonlyResult.error).toBe("alan salt okunur ya da pasif, başka bir alan seç ya da yeni snapshot al");
    expect(readonlyInput?.value).toBe("sabit");
    expect(disabledResult.ok).toBe(false);
    expect(disabledInput?.value).toBe("pasif");
  });
});

describe("type – korumalı alanlar (ORTAK BAĞLAM §1)", () => {
  const blocked: ReadonlyArray<readonly [string, string]> = [
    ["input[type=password]", `<input data-ref="1" type="password" value="onceki">`],
    ["autocomplete=current-password", `<input data-ref="1" autocomplete="current-password" value="onceki">`],
    ["autocomplete=new-password", `<input data-ref="1" autocomplete="new-password" value="onceki">`],
    ["autocomplete=cc-number", `<input data-ref="1" autocomplete="cc-number" value="onceki">`],
    ["autocomplete=cc-csc", `<input data-ref="1" autocomplete="cc-csc" value="onceki">`],
    ["büyük harfli CC-NUMBER", `<input data-ref="1" autocomplete="CC-NUMBER" value="onceki">`],
    ["type=PASSWORD", `<input data-ref="1" type="PASSWORD" value="onceki">`],
  ];

  for (const [label, markup] of blocked) {
    it(`${label} reddedilir ve alan değişmez`, async () => {
      mount(markup);
      const input = document.querySelector("input") as HTMLInputElement;
      const seen = recordEvents(input, ["input", "change"]);

      const result = await actions.type({ ref: 1, text: "4111111111111111" });

      expect(result.ok).toBe(false);
      expect(result.data).toBeNull();
      expect(result.error).toContain("password_field_blocked");
      expect(input.value).toBe("onceki");
      expect(seen).toEqual([]);
    });
  }

  it("korumalı alan submit:true ile de yazılmaz", async () => {
    mount(`<form data-ref="9"><input data-ref="1" type="password" value="onceki"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "gizli", submit: true });

    expect(result.ok).toBe(false);
    expect(requestSubmit).not.toHaveBeenCalled();
  });
});

describe("type – korumalı alan ADINDAN tanınır (type=text CVV kutusu)", () => {
  const blockedByName: ReadonlyArray<readonly [string, string]> = [
    ["name=cvv", `<input data-ref="1" name="cvv" value="onceki">`],
    ["name=card_number", `<input data-ref="1" name="card_number" value="onceki">`],
    ["id=kredi-kart", `<input data-ref="1" id="kredi-kart" value="onceki">`],
    ["placeholder=Şifre", `<input data-ref="1" placeholder="Şifre" value="onceki">`],
    ["aria-label=Güvenlik kodu", `<input data-ref="1" aria-label="Güvenlik kodu" value="onceki">`],
    ["name=iban", `<input data-ref="1" name="iban" value="onceki">`],
    ["name=pin", `<input data-ref="1" name="pin" value="onceki">`],
    ["data-testid=passphrase", `<input data-ref="1" data-testid="passphrase" value="onceki">`],
  ];

  for (const [label, markup] of blockedByName) {
    it(`${label} reddedilir`, async () => {
      mount(markup);

      const result = await actions.type({ ref: 1, text: "123" });

      expect(result.ok).toBe(false);
      expect(result.error).toContain("password_field_blocked");
      expect((document.querySelector("input") as HTMLInputElement).value).toBe("onceki");
    });
  }

  const allowedNames: ReadonlyArray<readonly [string, string]> = [
    ['name="cc" (karbon kopya)', `<input data-ref="1" name="cc">`],
    ["name=email", `<input data-ref="1" name="email">`],
    ["aria-label=Kart adı yok", `<input data-ref="1" aria-label="Arama">`],
    ["name=pinned_note", `<input data-ref="1" name="pinned_note">`],
  ];

  for (const [label, markup] of allowedNames) {
    it(`${label} engellenmez`, async () => {
      mount(markup);

      const result = await actions.type({ ref: 1, text: "deger" });

      expect(result.ok).toBe(true);
      expect((document.querySelector("input") as HTMLInputElement).value).toBe("deger");
    });
  }
});

describe("type – pasif ve yazılamaz alanlar", () => {
  it("aria-disabled alan reddedilir", async () => {
    mount(`<input data-ref="1" aria-disabled="true" value="onceki">`);

    const result = await actions.type({ ref: 1, text: "x" });

    expect(result.ok).toBe(false);
    expect(result.error).toContain("pasif");
    expect((document.querySelector("input") as HTMLInputElement).value).toBe("onceki");
  });

  const nonTypable = ["checkbox", "radio", "file", "submit", "button", "reset"];

  for (const inputType of nonTypable) {
    it(`input[type=${inputType}] metin kabul etmez, click önerilir`, async () => {
      mount(`<input data-ref="1" type="${inputType}">`);

      const result = await actions.type({ ref: 1, text: "x" });

      expect(result.ok).toBe(false);
      expect(result.error).toContain(`input type=${inputType}`);
      expect(result.error).toContain("click kullan");
    });
  }

  it("maxlength sınırını aşan metin kırpılır", async () => {
    mount(`<input data-ref="1" maxlength="5">`);

    const result = await actions.type({ ref: 1, text: "0123456789" });

    expect(result.ok).toBe(true);
    expect((document.querySelector("input") as HTMLInputElement).value).toBe("01234");
  });
});

describe("type – contenteditable", () => {
  it("contenteditable öğeye execCommand('insertText') ile yazar", async () => {
    mount(`<div data-ref="4" contenteditable="true">eski metin</div>`);
    const editable = document.querySelector("div[contenteditable]") as HTMLElement;
    const execSpy = vi.spyOn(document, "execCommand");

    const result = await actions.type({ ref: 4, text: "yeni metin" });

    expect(result).toEqual({ ok: true, data: { ok: true, submitted: false }, error: null });
    expect(execSpy).toHaveBeenCalledWith("insertText", false, "yeni metin");
    // Stub'ın uyguladığı sonuç (gerçek tarayıcı davranışı değil, bkz. başlık):
    expect(editable.textContent).toBe("yeni metin");
  });

  it("execCommand false dönerse düz metin yoluna düşer", async () => {
    mount(`<div data-ref="4" contenteditable="true">eski metin</div>`);
    const editable = document.querySelector("div[contenteditable]") as HTMLElement;
    vi.spyOn(document, "execCommand").mockImplementation(() => false);
    const seen = recordEvents(editable, ["input", "change"]);

    const result = await actions.type({ ref: 4, text: "yedek yol" });

    expect(result.ok).toBe(true);
    expect(editable.textContent).toBe("yedek yol");
    expect(seen).toEqual(["input", "change"]);
  });

  it("execCommand fırlatsa bile yedek yol çalışır", async () => {
    mount(`<div data-ref="4" contenteditable="true">eski</div>`);
    vi.spyOn(document, "execCommand").mockImplementation(() => {
      throw new Error("not supported");
    });

    const result = await actions.type({ ref: 4, text: "yedek" });

    expect(result.ok).toBe(true);
    expect((document.querySelector("div[contenteditable]") as HTMLElement).textContent).toBe(
      "yedek",
    );
  });

  it("contenteditable=false öğeye yazmayı reddeder", async () => {
    mount(`<div data-ref="4" contenteditable="false">metin</div>`);

    const result = await actions.type({ ref: 4, text: "x" });

    expect(result.ok).toBe(false);
    expect(result.error).toContain("bu öğeye yazılamaz");
  });
});

describe("type – select", () => {
  it("option metnine göre seçer", async () => {
    mount(`<select data-ref="2"><option value="s">Small</option><option value="l">Large</option></select>`);
    const select = document.querySelector("select") as HTMLSelectElement;
    const seen = recordEvents(select, ["input", "change"]);

    const result = await actions.type({ ref: 2, text: "Large" });

    expect(result.ok).toBe(true);
    expect(select.value).toBe("l");
    expect(seen).toEqual(["input", "change"]);
  });

  it("option value'suna göre de seçer", async () => {
    mount(`<select data-ref="2"><option value="s">Small</option><option value="l">Large</option></select>`);

    await actions.type({ ref: 2, text: "s" });

    expect((document.querySelector("select") as HTMLSelectElement).value).toBe("s");
  });

  it("bulunamayan seçenekte mevcut seçenekleri listeler", async () => {
    mount(`<select data-ref="2"><option value="s">Small</option><option value="l">Large</option></select>`);

    const result = await actions.type({ ref: 2, text: "XXL" });

    expect(result.ok).toBe(false);
    expect(result.error).toContain("seçenek bulunamadı");
    expect(result.error).toContain("Small | Large");
  });
});

describe("type – ref ve argüman doğrulama", () => {
  it("olmayan ref için 'yeni snapshot al' der", async () => {
    mount(`<input data-ref="1">`);

    const result = await actions.type({ ref: 42, text: "x" });

    expect(result).toEqual({
      ok: false,
      data: null,
      error: "ref bulunamadı, yeni snapshot al",
    });
  });

  it("ref eksikse ya da sayı değilse açıklar", async () => {
    const missing = await actions.type({ text: "x" });
    const wrong = await actions.type({ ref: "abc", text: "x" });
    const float = await actions.type({ ref: 1.5, text: "x" });

    for (const result of [missing, wrong, float]) {
      expect(result.ok).toBe(false);
      expect(result.error).toBe("ref eksik ya da sayı değil");
    }
  });

  it("sayı biçiminde string ref kabul edilir", async () => {
    mount(`<input data-ref="3">`);

    const result = await actions.type({ ref: "3", text: "tamam" });

    expect(result.ok).toBe(true);
    expect((document.querySelector("input") as HTMLInputElement).value).toBe("tamam");
  });

  it("text yoksa ya da metin değilse reddeder (ref çözüldükten sonra)", async () => {
    mount(`<input data-ref="1">`);

    const missing = await actions.type({ ref: 1 });
    const wrong = await actions.type({ ref: 1, text: 5 });

    for (const result of [missing, wrong]) {
      expect(result.ok).toBe(false);
      expect(result.error).toBe("text eksik ya da metin değil");
    }
  });
});

describe("type – submit", () => {
  it("submit:true form içindeyse form.requestSubmit çağırır", async () => {
    mount(`<form><input data-ref="1" aria-label="Arama"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "kitap", submit: true });

    expect(result.data).toEqual({ ok: true, submitted: true });
    expect(requestSubmit).toHaveBeenCalledTimes(1);
    expect((document.querySelector("input") as HTMLInputElement).value).toBe("kitap");
  });

  it("submit verilmezse requestSubmit çağrılmaz ve submitted false", async () => {
    mount(`<form><input data-ref="1"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "kitap" });

    expect(result.data).toEqual({ ok: true, submitted: false });
    expect(requestSubmit).not.toHaveBeenCalled();
  });

  it("submit:true form yoksa Enter tuş olaylarını gönderir", async () => {
    mount(`<input data-ref="1">`);
    const input = document.querySelector("input") as HTMLInputElement;
    const keys: KeyboardEvent[] = [];
    for (const type of ["keydown", "keypress", "keyup"]) {
      input.addEventListener(type, (event) => keys.push(event as KeyboardEvent));
    }

    const result = await actions.type({ ref: 1, text: "ara", submit: true });

    expect(result.data).toEqual({ ok: true, submitted: true });
    expect(keys.map((event) => event.type)).toEqual(["keydown", "keypress", "keyup"]);
    expect(keys.every((event) => event.key === "Enter" && event.keyCode === 13)).toBe(true);
  });

  it('form="baska-id" ile ilişkili alan o formu gönderir', async () => {
    mount(`<form id="dis"><input name="q"></form><input data-ref="1" form="dis">`);
    const form = document.getElementById("dis") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "kitap", submit: true });

    expect(result.data).toEqual({ ok: true, submitted: true });
    expect(requestSubmit).toHaveBeenCalledTimes(1);
  });

  it("HTML5 doğrulaması engelliyorsa dürüst rapor verir (metin yazıldı, gönderilmedi)", async () => {
    mount(`<form><input name="eposta" required><input data-ref="1" name="konu"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "konu metni", submit: true });

    expect(result.ok).toBe(false);
    expect(result.error).toContain("metin yazıldı ama form gönderilmedi");
    expect(result.error).toContain("eposta");
    expect(requestSubmit).not.toHaveBeenCalled();
    // Metin gerçekten yazıldı: model aynı metni ikinci kez yazmasın.
    expect((document.querySelector('[name="konu"]') as HTMLInputElement).value).toBe("konu metni");
  });

  it("novalidate formda doğrulama kontrolü atlanır", async () => {
    mount(`<form novalidate><input name="eposta" required><input data-ref="1"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "x", submit: true });

    expect(result.data).toEqual({ ok: true, submitted: true });
    expect(requestSubmit).toHaveBeenCalledTimes(1);
  });

  it("requestSubmit fırlatırsa submit butonuna tıklanır", async () => {
    mount(`<form><input data-ref="1"><button type="submit">Gönder</button></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    vi.spyOn(form, "requestSubmit").mockImplementation(() => {
      throw new Error("not supported");
    });
    const button = document.querySelector("button") as HTMLButtonElement;
    const clicks: string[] = [];
    button.addEventListener("click", () => clicks.push("click"));

    const result = await actions.type({ ref: 1, text: "x", submit: true });

    expect(result.data).toEqual({ ok: true, submitted: true });
    expect(clicks).toEqual(["click"]);
  });

  it("submit:true string '1' gibi truthy değerlerde tetiklenmez (yalnız === true)", async () => {
    mount(`<form><input data-ref="1"></form>`);
    const form = document.querySelector("form") as HTMLFormElement;
    const requestSubmit = vi.spyOn(form, "requestSubmit").mockImplementation(() => undefined);

    const result = await actions.type({ ref: 1, text: "x", submit: "yes" });

    expect(result.data).toEqual({ ok: true, submitted: false });
    expect(requestSubmit).not.toHaveBeenCalled();
  });
});

describe("click", () => {
  it("öğeyi görünüre kaydırır ve tıklar", async () => {
    mount(`<button data-ref="1">Sepete ekle</button>`);
    const button = document.querySelector("button") as HTMLButtonElement;
    const clicks: string[] = [];
    button.addEventListener("click", () => clicks.push("click"));

    const result = await actions.click({ ref: 1 });

    expect(result).toEqual({ ok: true, data: { ok: true }, error: null });
    expect(clicks).toEqual(["click"]);
    expect(button.scrollIntoView).toHaveBeenCalled();
  });

  it("olmayan ref için 'yeni snapshot al' der", async () => {
    const result = await actions.click({ ref: 5 });

    expect(result.ok).toBe(false);
    expect(result.error).toBe("ref bulunamadı, yeni snapshot al");
  });

  it("pasif öğeye tıklamayı reddeder (sessiz başarı yok)", async () => {
    mount(`<button data-ref="1" disabled>Pasif</button><div data-ref="2" role="button" aria-disabled="true">ARIA pasif</div>`);
    const clicks: string[] = [];
    for (const element of Array.from(document.querySelectorAll("[data-ref]"))) {
      element.addEventListener("click", () => clicks.push("click"));
    }

    const disabledResult = await actions.click({ ref: 1 });
    const ariaResult = await actions.click({ ref: 2 });

    for (const result of [disabledResult, ariaResult]) {
      expect(result.ok).toBe(false);
      expect(result.error).toContain("pasif");
    }
    expect(clicks).toEqual([]);
  });

  it("odaklanamayan öğede bile tıklar (focus hatası yutulur)", async () => {
    mount(`<div data-ref="1" role="button">Özel</div>`);
    const element = document.querySelector("div[role=button]") as HTMLElement;
    vi.spyOn(element, "focus").mockImplementation(() => {
      throw new Error("not focusable");
    });
    const clicks: string[] = [];
    element.addEventListener("click", () => clicks.push("click"));

    const result = await actions.click({ ref: 1 });

    expect(result.ok).toBe(true);
    expect(clicks).toEqual(["click"]);
  });
});

describe("scroll", () => {
  it('direction "up"/"down" dışındaki değeri reddeder', async () => {
    for (const direction of [undefined, "left", 1, null]) {
      const result = await actions.scroll({ direction });
      expect(result.ok).toBe(false);
      expect(result.error).toBe('direction "up" ya da "down" olmalı');
    }
  });

  it("down için viewport'un %80'i kadar aşağı kaydırır", async () => {
    const scrollBy = vi.fn();
    (window as unknown as { scrollBy: unknown }).scrollBy = scrollBy;

    const result = await actions.scroll({ direction: "down" });

    expect(result.ok).toBe(true);
    expect(scrollBy).toHaveBeenCalledWith({
      top: Math.round(window.innerHeight * 0.8),
      behavior: "auto",
    });
    expect(typeof result.data?.scroll_y).toBe("number");
  });

  it("up için aynı miktarda yukarı kaydırır", async () => {
    const scrollBy = vi.fn();
    (window as unknown as { scrollBy: unknown }).scrollBy = scrollBy;

    await actions.scroll({ direction: "up" });

    expect(scrollBy).toHaveBeenCalledWith({
      top: -Math.round(window.innerHeight * 0.8),
      behavior: "auto",
    });
  });
});

describe("read", () => {
  it("ref verilmezse ana içeriğin metnini döner", async () => {
    document.body.innerHTML = `<main><p>Ürün açıklaması</p></main><nav><p>Menü</p></nav>`;

    const result = await actions.read({});

    expect(result.ok).toBe(true);
    expect(result.data?.text).toContain("Ürün açıklaması");
    expect(result.data?.text).not.toContain("Menü");
  });

  it("ref verilirse yalnız o öğenin metnini döner", async () => {
    mount(`<main><p>Dışarıda</p><section data-ref="2"><p>İçeride</p></section></main>`);

    const result = await actions.read({ ref: 2 });

    expect(result.data?.text).toBe("İçeride");
  });

  it("metni olmayan input için value'ya düşer", async () => {
    mount(`<input data-ref="1" value="ahmet@example.com">`);

    const result = await actions.read({ ref: 1 });

    expect(result.data?.text).toBe("ahmet@example.com");
  });

  it("select için yalnız seçili option'ın metnini döner", async () => {
    mount(`<select data-ref="2"><option value="s">Small</option><option value="l" selected>Large</option></select>`);

    const result = await actions.read({ ref: 2 });

    expect(result.data?.text).toBe("Large");
  });

  it("checkbox için true/false döner", async () => {
    mount(`<input data-ref="1" type="checkbox" checked><input data-ref="2" type="checkbox">`);

    expect((await actions.read({ ref: 1 })).data?.text).toBe("true");
    expect((await actions.read({ ref: 2 })).data?.text).toBe("false");
  });

  it("korumalı alanın değeri read ile de sızmaz", async () => {
    mount(`
      <input data-ref="1" type="password" value="hunter2">
      <input data-ref="2" name="cvv" value="123">
      <input data-ref="3" type="password">
    `);

    const password = await actions.read({ ref: 1 });
    const cvv = await actions.read({ ref: 2 });
    const empty = await actions.read({ ref: 3 });

    expect(password.data?.text).toBe("••••");
    expect(cvv.data?.text).toBe("••••");
    expect(empty.data?.text).toBe("");
    expect(JSON.stringify([password, cvv])).not.toContain("hunter2");
  });

  it("olmayan ref için 'yeni snapshot al' der", async () => {
    const result = await actions.read({ ref: 77 });

    expect(result.ok).toBe(false);
    expect(result.error).toBe("ref bulunamadı, yeni snapshot al");
  });
});

describe("zarf şekli", () => {
  it("her yol {ok, data, error} döner ve hiçbiri exception sızdırmaz", async () => {
    mount(`<input data-ref="1">`);
    const results = [
      await actions.type({ ref: 1, text: "x" }),
      await actions.type({ ref: 999, text: "x" }),
      await actions.type({}),
      await actions.click({ ref: 1 }),
      await actions.click({}),
      await actions.scroll({}),
      await actions.read({}),
      await actions.read({ ref: 999 }),
    ];

    for (const result of results) {
      expect(Object.keys(result).sort()).toEqual(["data", "error", "ok"]);
      expect(typeof result.ok).toBe("boolean");
      expect(result.ok ? result.error : result.data).toBeNull();
    }
  });
});
