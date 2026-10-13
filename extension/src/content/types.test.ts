/**
 * `types.ts` tests (K2): the `{ok, data, error}` envelope and the frozen
 * contract constants (ORTAK BAĞLAM §7). No DOM, no stubs needed here.
 */

import { describe, expect, it } from "vitest";
import {
  BROWSER_TOOLS,
  MAX_ELEMENTS,
  MAX_NAME_LENGTH,
  MAX_READ_LENGTH,
  MAX_TEXT_EXCERPT,
  fail,
  ok,
} from "./types";

describe("ok()", () => {
  it("data'yı sarar, error null olur", () => {
    expect(ok({ scroll_y: 120 })).toEqual({
      ok: true,
      data: { scroll_y: 120 },
      error: null,
    });
  });

  it("zarf yalnız ok/data/error anahtarlarını taşır", () => {
    expect(Object.keys(ok("x")).sort()).toEqual(["data", "error", "ok"]);
  });

  it("null data da başarı sayılır (fail ile karışmaz)", () => {
    const result = ok(null);

    expect(result.ok).toBe(true);
    expect(result.data).toBeNull();
    expect(result.error).toBeNull();
  });

  it("verilen referansı kopyalamaz (aynı nesne döner)", () => {
    const payload = { elements: [] };

    expect(ok(payload).data).toBe(payload);
  });

  it("JSON'a çevrilebilir (tool_result frame'i için)", () => {
    expect(JSON.parse(JSON.stringify(ok({ text: "merhaba" })))).toEqual({
      ok: true,
      data: { text: "merhaba" },
      error: null,
    });
  });
});

describe("fail()", () => {
  it("mesajı taşır, data null olur", () => {
    expect(fail("ref bulunamadı, yeni snapshot al")).toEqual({
      ok: false,
      data: null,
      error: "ref bulunamadı, yeni snapshot al",
    });
  });

  it("zarf yalnız ok/data/error anahtarlarını taşır", () => {
    expect(Object.keys(fail("x")).sort()).toEqual(["data", "error", "ok"]);
  });

  it("boş mesaj bile ok:false bırakır", () => {
    const result = fail("");

    expect(result.ok).toBe(false);
    expect(result.error).toBe("");
    expect(result.data).toBeNull();
  });
});

describe("sözleşme sabitleri", () => {
  it("limitler ORTAK BAĞLAM §7 ile aynı", () => {
    expect(MAX_ELEMENTS).toBe(150);
    expect(MAX_NAME_LENGTH).toBe(80);
    expect(MAX_TEXT_EXCERPT).toBe(1500);
    expect(MAX_READ_LENGTH).toBe(4000);
  });

  it("tool listesi tam ve browser.* ön ekli", () => {
    expect([...BROWSER_TOOLS]).toEqual([
      "browser.snapshot",
      "browser.click",
      "browser.type",
      "browser.scroll",
      "browser.navigate",
      "browser.open",
      "browser.read",
      "browser.highlight",
    ]);
    expect(BROWSER_TOOLS.every((name) => name.startsWith("browser."))).toBe(true);
    expect(new Set(BROWSER_TOOLS).size).toBe(BROWSER_TOOLS.length);
  });
});
