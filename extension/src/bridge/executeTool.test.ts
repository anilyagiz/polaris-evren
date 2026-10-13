/**
 * `executeTool.ts` tests (K2 bridge).
 *
 * ---------------------------------------------------------------------------
 * ORTAM NOTU — NE STUB'LANDI
 * ---------------------------------------------------------------------------
 * jsdom'un layout eksikliği burada sorun değil: bu dosya DOM'a dokunmaz.
 * Stub'lanan tek şey `globalThis.chrome` — extension API'si Node'da hiç yok,
 * bu yüzden tamamı sahte (`installChrome`). Sahte API gerçek Chrome'un gözlenen
 * davranışını taklit eder:
 *
 *   - `chrome.tabs.update()` çağrıldığında `onUpdated` dinleyicilerine önce
 *     `{status:"loading"}` sonra `{status:"complete"}` gönderir (gerçek Chrome
 *     da böyle yapar). `watchTabLoad()` zaten bu sıraya dayanıyor; aksi halde
 *     her navigate testi 300 ms'lik yedek poller'ı beklerdi. Poller yolu ayrı
 *     bir testte kasıtlı olarak tek başına bırakılıyor.
 *   - `chrome.runtime.getURL("")` extension kök adresini döner; `isOwnPage()`
 *     bununla panelin kendi sekmesini ayıklıyor.
 *
 * Zaman aşımı testleri `vi.useFakeTimers()` kullanır: gerçek sürelerle
 * 3–8 saniye beklemek gerekirdi.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IS_MOCK, executeBrowserTool, isRestrictedUrl } from "./executeTool";

const EXTENSION_ROOT = "chrome-extension://polarisdevextensionid/";
const CONTENT_OK = { ok: true, data: { done: true }, error: null };

interface FakeTab {
  id?: number;
  url?: string;
  title?: string;
  status?: string;
}

type UpdatedListener = (
  tabId: number,
  change: { status?: string },
  tab: FakeTab,
) => void;

interface ChromeFake {
  tab: FakeTab;
  get: ReturnType<typeof vi.fn>;
  query: ReturnType<typeof vi.fn>;
  sendMessage: ReturnType<typeof vi.fn>;
  update: ReturnType<typeof vi.fn>;
  addUpdated: ReturnType<typeof vi.fn>;
  removeUpdated: ReturnType<typeof vi.fn>;
  executeScript: ReturnType<typeof vi.fn>;
  getManifest: ReturnType<typeof vi.fn>;
  /** Tool/ping messages the bridge sent, in order. */
  sent: Array<{ kind?: string; name?: string }>;
  /** Replay a real page load against the registered onUpdated listeners. */
  emitLoad(): void;
}

let fake: ChromeFake;

function installChrome(overrides: Partial<FakeTab> = {}): ChromeFake {
  const tab: FakeTab = {
    id: 7,
    url: "https://example.com/urun",
    title: "Örnek sayfa",
    status: "complete",
    ...overrides,
  };

  const updatedListeners = new Set<UpdatedListener>();
  const removedListeners = new Set<(tabId: number) => void>();
  const sent: Array<{ kind?: string; name?: string }> = [];

  const emitLoad = (): void => {
    tab.status = "loading";
    for (const listener of [...updatedListeners]) {
      listener(tab.id ?? -1, { status: "loading" }, tab);
    }
    tab.status = "complete";
    for (const listener of [...updatedListeners]) {
      listener(tab.id ?? -1, { status: "complete" }, tab);
    }
  };

  const chromeFake: ChromeFake = {
    tab,
    sent,
    emitLoad,
    get: vi.fn(async () => ({ ...tab })),
    query: vi.fn(async () => [tab]),
    sendMessage: vi.fn(async (_tabId: number, message: { kind?: string; name?: string }) => {
      sent.push(message);
      return CONTENT_OK;
    }),
    update: vi.fn(async (_tabId: number, info: { url: string }) => {
      tab.url = info.url;
      emitLoad();
      return { ...tab };
    }),
    addUpdated: vi.fn((listener: UpdatedListener) => updatedListeners.add(listener)),
    removeUpdated: vi.fn((listener: UpdatedListener) => updatedListeners.delete(listener)),
    executeScript: vi.fn(async () => [{ result: null }]),
    getManifest: vi.fn(() => ({ content_scripts: [{ js: ["src/content/index.ts"] }] })),
  };

  (globalThis as unknown as { chrome: unknown }).chrome = {
    tabs: {
      TAB_ID_NONE: -1,
      get: chromeFake.get,
      query: chromeFake.query,
      sendMessage: chromeFake.sendMessage,
      update: chromeFake.update,
      onUpdated: {
        addListener: chromeFake.addUpdated,
        removeListener: chromeFake.removeUpdated,
      },
      onRemoved: {
        addListener: (listener: (tabId: number) => void) => removedListeners.add(listener),
        removeListener: (listener: (tabId: number) => void) => removedListeners.delete(listener),
      },
    },
    scripting: { executeScript: chromeFake.executeScript },
    runtime: {
      getManifest: chromeFake.getManifest,
      getURL: (path: string) => `${EXTENSION_ROOT}${path}`,
    },
  };

  return chromeFake;
}

function expectEnvelope(result: unknown): void {
  expect(Object.keys(result as object).sort()).toEqual(["data", "error", "ok"]);
}

const MISSING_RECEIVER = "Could not establish connection. Receiving end does not exist.";

beforeEach(() => {
  fake = installChrome();
});

afterEach(() => {
  vi.useRealTimers();
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
});

describe("isRestrictedUrl", () => {
  const restricted = [
    "chrome://settings",
    "brave://newtab",
    "edge://extensions",
    "about:blank",
    "chrome-extension://abc/page.html",
    "moz-extension://abc/page.html",
    "devtools://devtools/bundled/x.html",
    "view-source:https://example.com",
    "CHROME://SETTINGS",
    "https://chrome.google.com/webstore/detail/foo",
    "https://chromewebstore.google.com/detail/foo",
  ];

  for (const url of restricted) {
    it(`kısıtlı: ${url}`, () => {
      expect(isRestrictedUrl(url)).toBe(true);
    });
  }

  const allowed = [
    "https://example.com",
    "http://localhost:8000/panel",
    // Web store adını yalnızca query'de taşıyan sayfa kilitlenmemeli.
    "https://example.com/ara?ref=chromewebstore.google.com",
    undefined,
    "",
  ];

  for (const url of allowed) {
    it(`kısıtlı değil: ${String(url)}`, () => {
      expect(isRestrictedUrl(url)).toBe(false);
    });
  }
});

describe("executeBrowserTool – tool adı doğrulama", () => {
  it("bilinmeyen tool adını reddeder ve chrome'a hiç dokunmaz", async () => {
    const result = await executeBrowserTool("browser.explode", {});

    expect(result).toEqual({ ok: false, data: null, error: "Bilinmeyen tool: browser.explode" });
    expect(fake.query).not.toHaveBeenCalled();
    expect(fake.sendMessage).not.toHaveBeenCalled();
  });

  it("desktop.*, boş ad ve yanlış harf büyüklüğü de reddedilir", async () => {
    for (const name of ["desktop.click", "", "snapshot", "browser.Snapshot"]) {
      const result = await executeBrowserTool(name, {});
      expect(result.ok).toBe(false);
      expect(result.error).toContain("Bilinmeyen tool");
    }
    expect(fake.sendMessage).not.toHaveBeenCalled();
  });

  it("bilinen tool'u content script'e {kind:'tool'} olarak iletir", async () => {
    const result = await executeBrowserTool("browser.click", { ref: 3 });

    expect(fake.sendMessage).toHaveBeenCalledWith(7, {
      kind: "tool",
      name: "browser.click",
      args: { ref: 3 },
    });
    expect(result).toEqual(CONTENT_OK);
  });

  it("args nesne değilse boş nesneye çevrilir", async () => {
    const result = await executeBrowserTool(
      "browser.snapshot",
      undefined as unknown as Record<string, unknown>,
    );

    expect(result.ok).toBe(true);
    expect(fake.sendMessage).toHaveBeenCalledWith(7, {
      kind: "tool",
      name: "browser.snapshot",
      args: {},
    });
  });
});

describe("executeBrowserTool – sekme çözümleme", () => {
  it("tabId verilirse chrome.tabs.get kullanılır, query çağrılmaz", async () => {
    await executeBrowserTool("browser.snapshot", {}, 42);

    expect(fake.get).toHaveBeenCalledWith(42);
    expect(fake.query).not.toHaveBeenCalled();
  });

  it("tabId yoksa önce lastFocusedWindow + windowType normal sorgulanır", async () => {
    await executeBrowserTool("browser.snapshot", {});

    expect(fake.query).toHaveBeenNthCalledWith(1, {
      active: true,
      lastFocusedWindow: true,
      windowType: "normal",
    });
  });

  it("ilk sorgu boşsa sıradaki sorgulara düşer", async () => {
    fake.query
      .mockImplementationOnce(async () => [])
      .mockImplementationOnce(async () => [fake.tab]);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(fake.query).toHaveBeenNthCalledWith(2, {
      active: true,
      currentWindow: true,
      windowType: "normal",
    });
    expect(result.ok).toBe(true);
  });

  it("panelin kendi extension sayfası hedef seçilmez", async () => {
    const ownPage: FakeTab = { id: 3, url: `${EXTENSION_ROOT}src/panel/index.html` };
    fake.query
      .mockImplementationOnce(async () => [ownPage])
      .mockImplementationOnce(async () => [ownPage, fake.tab]);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result.ok).toBe(true);
    expect(fake.sendMessage).toHaveBeenCalledWith(7, expect.anything());
  });

  it("tabId panelin KENDİ sekmesiyse (sekme yedeği) kullanıcının sayfası çözülür", async () => {
    // Panel tab olarak açıldığında {active, currentWindow} sorgusu panelin
    // kendisini döndürür ve id'si buraya gelir (gerçek tarayıcı E2E'sinde bulundu).
    const ownPage: FakeTab = { id: 3, url: `${EXTENSION_ROOT}src/panel/index.html` };
    fake.get.mockImplementationOnce(async () => ({ ...ownPage }));
    fake.query.mockImplementation(async () => [ownPage, fake.tab]);

    const result = await executeBrowserTool("browser.snapshot", {}, 3);

    expect(result.ok).toBe(true);
    expect(fake.sendMessage).toHaveBeenCalledWith(7, expect.anything());
    expect(fake.sendMessage).not.toHaveBeenCalledWith(3, expect.anything());
  });

  it("tabId kısıtlı bir web dışı sayfaysa (chrome://) sessizce başka sekmeye geçilmez", async () => {
    fake.get.mockImplementationOnce(async () => ({ id: 9, url: "chrome://settings" }));

    const result = await executeBrowserTool("browser.snapshot", {}, 9);

    expect(result).toEqual({ ok: false, data: null, error: "Bu sayfada asistan çalışamıyor" });
    expect(fake.query).not.toHaveBeenCalled();
  });

  it("hiç sekme yoksa sürülebilir sekme olmadığını söyler", async () => {
    fake.query.mockImplementation(async () => []);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual({ ok: false, data: null, error: "Sürülebilir sekme yok, kullanıcıdan bir sayfa açmasını iste" });
  });

  it("id'si olmayan ya da TAB_ID_NONE olan sekme kullanılmaz", async () => {
    fake.query.mockImplementation(async () => [
      { url: "https://example.com" },
      { id: -1, url: "https://example.com" },
    ]);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result.ok).toBe(false);
    expect(result.error).toBe("Sürülebilir sekme yok, kullanıcıdan bir sayfa açmasını iste");
  });

  it("query fırlatırsa sıradaki sorguya geçer, exception sızmaz", async () => {
    fake.query
      .mockImplementationOnce(() => {
        throw new Error("extension context invalidated");
      })
      .mockImplementationOnce(async () => [fake.tab]);

    const result = await executeBrowserTool("browser.snapshot", {});

    expectEnvelope(result);
    expect(result.ok).toBe(true);
  });
});

describe("executeBrowserTool – kısıtlı sayfalar", () => {
  const blockedPages = [
    "chrome://settings",
    "brave://settings/shields",
    "about:blank",
    "view-source:https://example.com",
    "https://chrome.google.com/webstore/category/extensions",
    "https://chromewebstore.google.com/detail/abc",
  ];

  for (const url of blockedPages) {
    it(`${url} üzerinde "Bu sayfada asistan çalışamıyor"`, async () => {
      fake = installChrome({ url });

      const result = await executeBrowserTool("browser.snapshot", {});

      expect(result).toEqual({
        ok: false,
        data: null,
        // Blank/new-tab pages add a hint: "open a site with browser.open".
        error: url === "about:blank"
          ? "Bu sayfada asistan çalışamıyor: sekme boş (yeni sekme). Bir site açmak için browser.open kullan"
          : "Bu sayfada asistan çalışamıyor",
      });
      expect(fake.sendMessage).not.toHaveBeenCalled();
    });
  }

  it("kısıtlı sayfadan ÇIKMAK için navigate'e izin verilir", async () => {
    fake = installChrome({ url: "brave://newtab" });

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.com" });

    expect(result.ok).toBe(true);
    expect(fake.update).toHaveBeenCalledWith(7, { url: "https://example.com/" });
  });
});

describe("executeBrowserTool – browser.navigate hedef doğrulama", () => {
  const rejectedTargets = [
    "javascript:alert(1)",
    "javascript://alert(1)",
    "JavaScript:void(0)",
    // Bu adres "https://" ön eki eklenince AYRIŞTIRILABİLİR hale gelir:
    // şema allow-list'i olmasa chrome.tabs.update'e kadar giderdi.
    "javascript:/**/alert(1)",
    "data:text/html,<h1>x</h1>",
    "data://text/html,x",
    "file:///C:/Windows/system.ini",
    "blob:https://example.com/abc",
    "chrome://settings",
    "chrome-extension://abc/page.html",
    "ftp://example.com/f",
    "https://chromewebstore.google.com/detail/abc",
  ];

  for (const target of rejectedTargets) {
    it(`hedef reddedilir: ${target}`, async () => {
      const result = await executeBrowserTool("browser.navigate", { url: target });

      expect(result.ok).toBe(false);
      expect(result.data).toBeNull();
      expect(typeof result.error).toBe("string");
      // Asıl güvenlik güvencesi: sekme HİÇ yönlendirilmedi.
      expect(fake.update).not.toHaveBeenCalled();
    });
  }

  it("javascript: ve data: için sekme adresi değişmeden kalır", async () => {
    await executeBrowserTool("browser.navigate", { url: "javascript:alert(1)" });
    await executeBrowserTool("browser.navigate", { url: "data:text/html,x" });

    expect(fake.tab.url).toBe("https://example.com/urun");
  });

  it("url eksikse ya da boşsa ne verileceğini söyler", async () => {
    for (const url of [undefined, "", "   ", 5, null]) {
      const result = await executeBrowserTool("browser.navigate", { url });
      expect(result.ok).toBe(false);
      expect(result.error).toBe("url eksik, http(s) ile başlayan bir adres ver");
    }
    expect(fake.update).not.toHaveBeenCalled();
  });

  it("ayrıştırılamayan url 'geçersiz url' döner", async () => {
    const result = await executeBrowserTool("browser.navigate", { url: "http://" });

    expect(result.ok).toBe(false);
    expect(result.error).toContain("geçersiz url");
    expect(fake.update).not.toHaveBeenCalled();
  });

  it("şemasız adrese https:// eklenir", async () => {
    const result = await executeBrowserTool("browser.navigate", { url: "example.com/ara?q=1" });

    expect(fake.update).toHaveBeenCalledWith(7, { url: "https://example.com/ara?q=1" });
    expect(result.ok).toBe(true);
  });

  it("host:port şema sanılmaz", async () => {
    await executeBrowserTool("browser.navigate", { url: "localhost:3000/x" });

    expect(fake.update).toHaveBeenCalledWith(7, { url: "https://localhost:3000/x" });
  });

  it("başarılı navigate sekmenin GERÇEK url ve title'ını döner (yönlendirme)", async () => {
    fake.update.mockImplementation(async () => {
      fake.tab.url = "https://example.com/yonlendirildi";
      fake.tab.title = "Yönlendirilen";
      fake.emitLoad();
      return { ...fake.tab };
    });

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.com/istek" });

    expect(result).toEqual({
      ok: true,
      data: { ok: true, url: "https://example.com/yonlendirildi", title: "Yönlendirilen" },
      error: null,
    });
  });

  it("onUpdated hiç gelmezse poller tab.status ile tamamlar", async () => {
    fake.update.mockImplementation(async (_tabId: number, info: { url: string }) => {
      fake.tab.url = info.url;
      return { ...fake.tab };
    });

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.com/yeni" });

    expect(result.ok).toBe(true);
    expect(fake.get).toHaveBeenCalled();
  });

  it("navigate content script'e mesaj göndermez", async () => {
    await executeBrowserTool("browser.navigate", { url: "https://example.com" });

    expect(fake.sendMessage).not.toHaveBeenCalled();
  });

  it("sayfa yüklenmezse zaman aşımı zarfı döner", async () => {
    vi.useFakeTimers();
    fake.update.mockImplementation(async () => ({ ...fake.tab }));
    fake.get.mockImplementation(async () => ({ ...fake.tab, status: "loading" }));

    const pending = executeBrowserTool("browser.navigate", { url: "https://example.com/yeni" });
    await vi.advanceTimersByTimeAsync(16000);
    const result = await pending;

    expectEnvelope(result);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("timeout");
  });
});

describe("executeBrowserTool – content script enjeksiyonu", () => {
  it("'Receiving end does not exist' hatasında enjekte edip BİR KEZ tekrar dener", async () => {
    fake.sendMessage
      .mockImplementationOnce(async () => {
        throw new Error(MISSING_RECEIVER);
      })
      .mockImplementationOnce(async (_tabId: number, message: { kind: string }) => {
        fake.sent.push(message);
        return { ok: true, data: { ready: true }, error: null };
      })
      .mockImplementationOnce(async (_tabId: number, message: { kind: string }) => {
        fake.sent.push(message);
        return CONTENT_OK;
      });

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual(CONTENT_OK);
    expect(fake.executeScript).toHaveBeenCalledTimes(1);
    expect(fake.executeScript).toHaveBeenCalledWith({
      target: { tabId: 7, allFrames: false },
      files: ["src/content/index.ts"],
    });
    // 1. tool (başarısız) → 2. ping (hazırlık yoklaması) → 3. tool (başarılı)
    expect(fake.sendMessage).toHaveBeenCalledTimes(3);
    expect(fake.sent.map((message) => message.kind)).toEqual(["ping", "tool"]);
  });

  it("enjeksiyondan sonra ikinci deneme de başarısızsa yeniden enjekte etmez", async () => {
    let calls = 0;
    fake.sendMessage.mockImplementation(async (_tabId: number, message: { kind: string }) => {
      calls += 1;
      if (message.kind === "ping") {
        return { ok: true, data: { ready: true }, error: null };
      }
      throw new Error(MISSING_RECEIVER);
    });

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual({
      ok: false,
      data: null,
      error: "Sayfaya bağlanılamadı, sekmeyi yenileyip tekrar dene",
    });
    expect(fake.executeScript).toHaveBeenCalledTimes(1);
    expect(calls).toBe(3);
  });

  it("content script hiç hazır olmazsa ping yoklaması sınırlıdır (sonsuz döngü yok)", async () => {
    vi.useFakeTimers();
    fake.sendMessage.mockImplementation(async () => {
      throw new Error(MISSING_RECEIVER);
    });

    const pending = executeBrowserTool("browser.snapshot", {});
    await vi.advanceTimersByTimeAsync(9000);
    const result = await pending;

    expect(result.ok).toBe(false);
    expect(result.error).toBe("Sayfaya bağlanılamadı, sekmeyi yenileyip tekrar dene");
    expect(fake.executeScript).toHaveBeenCalledTimes(1);
  });

  it("sadece 'could not establish connection' metni de tekrar denemeyi tetikler", async () => {
    fake.sendMessage
      .mockImplementationOnce(async () => {
        throw new Error("Could not establish connection.");
      })
      .mockImplementation(async () => CONTENT_OK);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual(CONTENT_OK);
    expect(fake.executeScript).toHaveBeenCalledTimes(1);
  });

  it("başka bir hata enjeksiyon tetiklemez, Chrome hatası çevrilip döner", async () => {
    fake.sendMessage.mockImplementation(async () => {
      throw new Error("Tab is discarded");
    });

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual({
      ok: false,
      data: null,
      error: "Sekme uykuya alınmış, önce sekmeye geç",
    });
    expect(fake.executeScript).not.toHaveBeenCalled();
    expect(fake.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("scripting izni yoksa açık bir hata döner", async () => {
    fake.sendMessage.mockImplementation(async () => {
      throw new Error(MISSING_RECEIVER);
    });
    (globalThis as unknown as { chrome: { scripting?: unknown } }).chrome.scripting = undefined;

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result.ok).toBe(false);
    expect(result.error).toContain('"scripting" izni yok');
  });

  it("manifest'te content script yoksa açık bir hata döner", async () => {
    fake.sendMessage.mockImplementation(async () => {
      throw new Error(MISSING_RECEIVER);
    });
    fake.getManifest.mockImplementation(() => ({ content_scripts: [] }));

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result.ok).toBe(false);
    expect(result.error).toBe("manifest'te content script tanımlı değil");
  });
});

describe("executeBrowserTool – sayfadan gelen yanıtın zarfı", () => {
  it("sendResponse çağrılmazsa (undefined) geçersiz yanıt denir", async () => {
    fake.sendMessage.mockImplementation(async () => undefined);

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual({ ok: false, data: null, error: "Sayfadan geçersiz yanıt geldi, yeni bir snapshot al" });
  });

  it("ok:false ama error'suz yanıta varsayılan mesaj konur", async () => {
    fake.sendMessage.mockImplementation(async () => ({ ok: false }));

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result).toEqual({ ok: false, data: null, error: "Bilinmeyen sayfa hatası" });
  });

  it("ok alanı truthy ama true değilse başarı sayılmaz", async () => {
    fake.sendMessage.mockImplementation(async () => ({ ok: "yes", data: { x: 1 }, error: null }));

    const result = await executeBrowserTool("browser.snapshot", {});

    expect(result.ok).toBe(false);
  });
});

describe("executeBrowserTool – hiçbir yolda exception sızmaz", () => {
  it("chrome.tabs.get reddederse zarf döner", async () => {
    fake.get.mockImplementation(async () => {
      throw new Error("No tab with id: 42");
    });

    const result = await executeBrowserTool("browser.snapshot", {}, 42);

    expectEnvelope(result);
    expect(result).toEqual({
      ok: false,
      data: null,
      error: "Sekme artık yok, yeni bir snapshot al",
    });
  });

  it("Error olmayan (string) ret sebebi de mesaja çevrilir", async () => {
    fake.sendMessage.mockImplementation(async () => {
      throw "düz string hata";
    });

    const result = await executeBrowserTool("browser.read", {});

    expectEnvelope(result);
    expect(result.error).toBe("düz string hata");
  });

  it("getManifest fırlatırsa zarf döner", async () => {
    fake.sendMessage.mockImplementation(async () => {
      throw new Error(MISSING_RECEIVER);
    });
    fake.getManifest.mockImplementation(() => {
      throw new Error("manifest okunamadı");
    });

    const result = await executeBrowserTool("browser.snapshot", {});

    expectEnvelope(result);
    expect(result.error).toBe("manifest okunamadı");
  });

  it("chrome.tabs.update reddederse navigate zarf döner (Chrome hatası çevrilir)", async () => {
    fake.update.mockImplementation(async () => {
      throw new Error("Cannot access contents of url");
    });

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.com" });

    expectEnvelope(result);
    expect(result.error).toBe("Bu sayfada asistan çalışamıyor");
  });

  it("content script askıda kalırsa zaman aşımı zarfı döner", async () => {
    vi.useFakeTimers();
    fake.sendMessage.mockImplementation(() => new Promise(() => undefined));

    const pending = executeBrowserTool("browser.snapshot", {});
    await vi.advanceTimersByTimeAsync(8100);
    const result = await pending;

    expectEnvelope(result);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("timeout");
    expect(result.error).toContain("browser.snapshot");
    // Model'e "işlem yine de olmuş olabilir" ipucu verilir.
    expect(result.error).toContain("snapshot");
  });

  it("tüm senaryolar aynı zarf şeklini korur", async () => {
    const scenarios: Array<() => Promise<unknown>> = [
      () => executeBrowserTool("yok", {}),
      () => executeBrowserTool("browser.snapshot", {}),
      () => executeBrowserTool("browser.click", { ref: 1 }),
      () => executeBrowserTool("browser.navigate", { url: "javascript:1" }),
      () => executeBrowserTool("browser.navigate", { url: "https://example.com" }),
      () => executeBrowserTool("browser.type", { ref: 1, text: "x" }),
      () => executeBrowserTool("browser.highlight", { ref: 1 }),
      () => executeBrowserTool("browser.scroll", { direction: "down" }),
      () => executeBrowserTool("browser.read", {}),
    ];

    for (const scenario of scenarios) {
      const result = await scenario();
      expectEnvelope(result);
      const envelope = result as { ok: boolean; data: unknown; error: unknown };
      expect(typeof envelope.ok).toBe("boolean");
      if (envelope.ok) {
        expect(envelope.error).toBeNull();
      } else {
        expect(envelope.data).toBeNull();
        expect(typeof envelope.error).toBe("string");
      }
    }
  });
});

describe("executeTool modül sözleşmesi", () => {
  it("IS_MOCK false (gerçek köprü devrede)", () => {
    expect(IS_MOCK).toBe(false);
  });
});

/**
 * Chrome's errors land in the model's context through `tool_result`. An English
 * sentence written for an extension developer tells the model nothing it can
 * act on, so each known family is rewritten; unknown ones must survive intact
 * rather than be guessed at.
 */
describe("Chrome hatalarının modele uygun metne çevrilmesi", () => {
  const cases: readonly (readonly [string, string])[] = [
    ["Tab is discarded", "Sekme uykuya alınmış, önce sekmeye geç"],
    ["No tab with id: 7", "Sekme artık yok, yeni bir snapshot al"],
    ["Invalid tab ID: 7", "Sekme artık yok, yeni bir snapshot al"],
    ["Cannot access contents of url \"https://x\"", "Bu sayfada asistan çalışamıyor"],
    [
      "Extension context invalidated.",
      "Eklenti yeniden yüklendi, sekmeyi yenilemeden işlem yapılamaz",
    ],
    ["No frame with id 3 in tab 4", "Sayfanın o bölümü kayboldu, yeni bir snapshot al"],
    [
      "The message port closed before a response was received.",
      "Sayfa cevap vermeden kapandı (muhtemelen yönlendirme), yeni bir snapshot al",
    ],
  ];

  for (const [chromeError, expected] of cases) {
    it(`"${chromeError.slice(0, 28)}…" → modele yön veren cümle`, async () => {
      fake.get.mockImplementation(async () => {
        throw new Error(chromeError);
      });

      const result = await executeBrowserTool("browser.snapshot", {}, 9);

      expect(result.ok).toBe(false);
      expect(result.error).toBe(expected);
    });
  }

  it("tanınmayan hata olduğu gibi geçer, uydurulmaz", async () => {
    fake.get.mockImplementation(async () => {
      throw new Error("Something nobody mapped yet");
    });

    const result = await executeBrowserTool("browser.snapshot", {}, 9);

    expect(result.error).toBe("Something nobody mapped yet");
  });
});
