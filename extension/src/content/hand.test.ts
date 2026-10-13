/**
 * `hand.ts` tests: the visible Polaris cursor.
 *
 * jsdom has no layout and no `matchMedia`; both are stubbed per test. The
 * shadow root is CLOSED on purpose, so the page (and these tests) can only see
 * the host element — which is exactly what "no page impact" means.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as actions from "./actions";
import { REF_ATTRIBUTE } from "./dom";
import {
  HAND_HOST_ID,
  dativeSuffix,
  describeStep,
  handPosition,
  labelFor,
  pointAt,
  prefersReducedMotion,
  press,
  resetHand,
  typeProgressively,
} from "./hand";

function stubMotion(reduced: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduced && query.includes("reduce"),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })),
  );
  window.matchMedia = globalThis.matchMedia;
}

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    left, top, width, height, x: left, y: top, right: left + width, bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

function button(name: string, at = rect(100, 200, 80, 30)): HTMLButtonElement {
  const el = document.createElement("button");
  el.textContent = name;
  el.getBoundingClientRect = () => at;
  document.body.appendChild(el);
  return el;
}

// jsdom has no innerText; accessibleName() reads it for button labels.
if (Object.getOwnPropertyDescriptor(HTMLElement.prototype, "innerText") === undefined) {
  Object.defineProperty(HTMLElement.prototype, "innerText", {
    configurable: true,
    get(this: HTMLElement): string {
      return this.textContent ?? "";
    },
  });
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetHand();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  // jsdom has no matchMedia of its own; don't leak the stub.
  delete (window as { matchMedia?: unknown }).matchMedia;
  resetHand();
});

describe("hand overlay – shadow DOM, no layout impact", () => {
  it("draws into ONE closed shadow host that is fixed, zero-sized and click-through", async () => {
    stubMotion(true);
    const target = button("Ara");
    const before = document.body.getBoundingClientRect();

    await pointAt(target, "“Ara”ya tıklıyorum");

    const hosts = document.querySelectorAll(`#${HAND_HOST_ID}`);
    expect(hosts).toHaveLength(1);
    const host = hosts[0] as HTMLElement;
    // Outside <body>: nothing in the page's layout flow moves.
    expect(host.parentElement).toBe(document.documentElement);
    expect(host.style.position).toBe("fixed");
    expect(host.style.width).toBe("0px");
    expect(host.style.height).toBe("0px");
    expect(host.style.pointerEvents).toBe("none");
    expect(host.style.zIndex).toBe("2147483647");
    expect(host.getAttribute("aria-hidden")).toBe("true");
    // Closed root: the page cannot reach in, and the label text never leaks
    // into page text extraction.
    expect(host.shadowRoot).toBeNull();
    expect(host.textContent).toBe("");
    expect(document.body.textContent).toBe("Ara");
    expect(document.body.getBoundingClientRect()).toEqual(before);

    // A second action reuses the host instead of stacking overlays.
    await pointAt(button("Gönder"), "x");
    expect(document.querySelectorAll(`#${HAND_HOST_ID}`)).toHaveLength(1);
  });

  it("only one host-level mutation per page: later moves stay inside the shadow root", async () => {
    stubMotion(true);
    await pointAt(button("Bir"), "bir");
    const records: MutationRecord[] = [];
    const observer = new MutationObserver((list) => records.push(...list));
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });

    await pointAt(button("İki", rect(400, 300, 50, 20)), "iki");
    await press();
    await Promise.resolve();
    observer.disconnect();

    // Only the test's own <button> insertion — the cursor itself is invisible
    // to page observers (and to waitForDomIdle).
    expect(records.every((r) => r.target !== document.getElementById(HAND_HOST_ID))).toBe(true);
    expect(records.filter((r) => r.type === "attributes")).toHaveLength(0);
  });
});

describe("hand motion", () => {
  it("reduced motion: jumps to the target centre instantly, no waiting", async () => {
    stubMotion(true);
    expect(prefersReducedMotion()).toBe(true);
    vi.useFakeTimers();

    const done = vi.fn();
    void pointAt(button("Ara", rect(100, 200, 80, 30)), "x").then(done);
    await vi.advanceTimersByTimeAsync(0);

    expect(done).toHaveBeenCalled();
    expect(handPosition()).toEqual({ x: 140, y: 215 });
  });

  it("no matchMedia (jsdom/old embedders) counts as reduced motion", () => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    expect(prefersReducedMotion()).toBe(true);
  });

  it("normal motion: glides for 300–450 ms before resolving", async () => {
    stubMotion(false);
    vi.useFakeTimers();

    const done = vi.fn();
    void pointAt(button("Ara", rect(10, 10, 20, 20)), "x").then(done);
    await vi.advanceTimersByTimeAsync(250);
    expect(done).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(220);
    expect(done).toHaveBeenCalled();
    expect(handPosition()).toEqual({ x: 20, y: 20 });
  });

  it("progressive typing writes growing prefixes within ~1 s, skipped under reduced motion", async () => {
    stubMotion(false);
    vi.useFakeTimers();
    const writes: string[] = [];
    const text = "mekanik klavye";

    const finished = vi.fn();
    void typeProgressively(text, (partial) => writes.push(partial)).then(finished);
    await vi.advanceTimersByTimeAsync(1050);
    expect(finished).toHaveBeenCalled();

    expect(writes.length).toBeGreaterThan(3);
    expect(writes.every((w, i) => text.startsWith(w) && (i === 0 || w.length > writes[i - 1]!.length))).toBe(true);
    expect(writes.at(-1)!.length).toBeLessThan(text.length); // final value is the caller's job

    // Long text: still bounded (several characters per step).
    const longDone = vi.fn();
    void typeProgressively("x".repeat(400), () => undefined).then(longDone);
    await vi.advanceTimersByTimeAsync(1050);
    expect(longDone).toHaveBeenCalled();

    stubMotion(true);
    const none: string[] = [];
    await typeProgressively(text, (p) => none.push(p));
    expect(none).toEqual([]);
  });

  it("browser.type through actions ends with the exact full value (React-safe final set)", async () => {
    stubMotion(false);
    const input = document.createElement("input");
    input.setAttribute(REF_ATTRIBUTE, "3");
    input.setAttribute("placeholder", "Ara");
    document.body.appendChild(input);
    const values: string[] = [];
    input.addEventListener("input", () => values.push(input.value));

    const result = await actions.type({ ref: 3, text: "kulaklık" });

    expect(result.ok).toBe(true);
    expect(input.value).toBe("kulaklık");
    expect(values.length).toBeGreaterThan(2); // progressive keystrokes were visible
    expect(values.at(-1)).toBe("kulaklık");
  });
});

describe("Turkish labels", () => {
  it("dative suffix follows vowel harmony", () => {
    expect(dativeSuffix("Ara")).toBe("ya");
    expect(dativeSuffix("Gönder")).toBe("e");
    expect(dativeSuffix("Sepete ekle")).toBe("ye");
    expect(dativeSuffix("Giriş")).toBe("e");
    expect(dativeSuffix("Kitap")).toBe("a");
    expect(dativeSuffix("123")).toBeNull();
  });

  it("describes click / type / read / highlight", () => {
    expect(describeStep("click", button("Ara"))).toBe("“Ara”ya tıklıyorum");
    expect(describeStep("click", button("42"))).toBe("“42” öğesine tıklıyorum");

    const search = document.createElement("input");
    search.type = "search";
    document.body.appendChild(search);
    expect(describeStep("type", search)).toBe("Arama kutusuna yazıyorum");

    const mail = document.createElement("input");
    mail.setAttribute("aria-label", "E-posta");
    document.body.appendChild(mail);
    expect(describeStep("type", mail)).toBe("“E-posta” alanına yazıyorum");

    expect(describeStep("read", button("Fiyat"))).toBe("“Fiyat” bölümünü okuyorum");
    expect(describeStep("highlight", button("Sepet"))).toBe("Şunu gösteriyorum: “Sepet”");
  });

  it("an explicit `label` arg wins, trimmed and bounded", () => {
    expect(labelFor({ label: "  Kendi etiketim " }, "x")).toBe("Kendi etiketim");
    expect(labelFor({ label: 5 }, "yedek")).toBe("yedek");
    expect(labelFor({ label: "a".repeat(200) }, "x")).toHaveLength(80);
  });
});
