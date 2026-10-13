/**
 * `browser.open` + per-request task-tab tracking in the bridge.
 *
 * A multi-tab fake of `chrome.tabs`/`chrome.windows`: `create()` and `update()`
 * replay Chrome's "loading" → "complete" sequence on the next macrotask, like
 * the real browser does after the call resolves.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { executeBrowserTool, forgetTaskTab, resetTaskTabs, taskTabOf } from "./executeTool";

const EXTENSION_ROOT = "chrome-extension://polarisdevextensionid/";

interface FakeTab {
  id: number;
  url: string;
  title: string;
  status: string;
  windowId: number;
  index: number;
  active: boolean;
  lastAccessed?: number;
}

type Listener = (tabId: number, change: { status?: string }, tab: FakeTab) => void;

let tabs: Map<number, FakeTab>;
let nextId: number;
let listeners: Set<Listener>;
let sentTo: Array<{ tabId: number; name?: string }>;
let refuseUpdateOn: ((tab: FakeTab) => boolean) | null;
let chromeApi: {
  create: ReturnType<typeof vi.fn>;
  update: ReturnType<typeof vi.fn>;
  focusWindow: ReturnType<typeof vi.fn>;
};

function load(tab: FakeTab, url: string): void {
  setTimeout(() => {
    tab.url = url;
    tab.title = `Başlık ${new URL(url).hostname}`;
    tab.status = "loading";
    for (const l of [...listeners]) l(tab.id, { status: "loading" }, { ...tab });
    tab.status = "complete";
    for (const l of [...listeners]) l(tab.id, { status: "complete" }, { ...tab });
  }, 0);
}

function install(initial: Array<Partial<FakeTab>>): void {
  tabs = new Map();
  listeners = new Set();
  sentTo = [];
  refuseUpdateOn = null;
  nextId = 100;
  for (const [i, partial] of initial.entries()) {
    const tab: FakeTab = {
      id: partial.id ?? i + 1,
      url: "https://example.com/",
      title: "Örnek",
      status: "complete",
      windowId: 1,
      index: i,
      active: i === 0,
      ...partial,
    };
    tabs.set(tab.id, tab);
  }

  chromeApi = {
    create: vi.fn(async (props: { url: string; windowId?: number; index?: number; active?: boolean }) => {
      const tab: FakeTab = {
        id: nextId++,
        url: "",
        title: "",
        status: "loading",
        windowId: props.windowId ?? 1,
        index: props.index ?? tabs.size,
        active: props.active ?? true,
      };
      tabs.set(tab.id, tab);
      load(tab, props.url);
      return { ...tab };
    }),
    update: vi.fn(async (tabId: number, info: { url?: string; active?: boolean }) => {
      const tab = tabs.get(tabId);
      if (tab === undefined) throw new Error(`No tab with id: ${tabId}`);
      if (info.url !== undefined) {
        if (refuseUpdateOn?.(tab)) throw new Error("Cannot access a chrome:// URL");
        load(tab, info.url);
      }
      if (info.active !== undefined) tab.active = info.active;
      return { ...tab };
    }),
    focusWindow: vi.fn(async () => ({})),
  };

  (globalThis as unknown as { chrome: unknown }).chrome = {
    tabs: {
      TAB_ID_NONE: -1,
      get: vi.fn(async (id: number) => {
        const tab = tabs.get(id);
        if (tab === undefined) throw new Error(`No tab with id: ${id}`);
        return { ...tab };
      }),
      query: vi.fn(async () => [...tabs.values()].filter((t) => t.active).map((t) => ({ ...t }))),
      create: chromeApi.create,
      update: chromeApi.update,
      sendMessage: vi.fn(async (tabId: number, message: { name?: string }) => {
        sentTo.push({ tabId, name: message.name });
        return { ok: true, data: { tabId }, error: null };
      }),
      onUpdated: {
        addListener: (l: Listener) => listeners.add(l),
        removeListener: (l: Listener) => listeners.delete(l),
      },
      onRemoved: { addListener: () => undefined, removeListener: () => undefined },
    },
    windows: { WINDOW_ID_NONE: -1, update: chromeApi.focusWindow },
    scripting: { executeScript: vi.fn() },
    runtime: {
      getManifest: () => ({ content_scripts: [{ js: ["x.js"] }] }),
      getURL: (path: string) => `${EXTENSION_ROOT}${path}`,
    },
  };
}

beforeEach(() => {
  resetTaskTabs();
});

afterEach(() => {
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
});

describe("browser.open", () => {
  it("opens a NEW focused tab next to the request tab, waits for load, returns its id", async () => {
    install([{ id: 7, url: "https://example.com/", windowId: 3, index: 2 }]);

    const result = await executeBrowserTool("browser.open", { url: "tr.wikipedia.org" }, 7, "r_1");

    expect(result.ok).toBe(true);
    expect(result.data).toEqual({
      ok: true,
      url: "https://tr.wikipedia.org/",
      title: "Başlık tr.wikipedia.org",
      tab_id: 100,
      new_tab: true,
    });
    expect(chromeApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ url: "https://tr.wikipedia.org/", active: true, windowId: 3, index: 3, openerTabId: 7 }),
    );
    expect(chromeApi.focusWindow).toHaveBeenCalledWith(3, { focused: true });
    expect(taskTabOf("r_1")).toBe(100);
  });

  it("keeps the same allow-list as navigate (no javascript:/data:/chrome:)", async () => {
    install([{ id: 7 }]);
    for (const url of ["javascript:alert(1)", "data:text/html,x", "chrome://settings", "file:///C:/x"]) {
      const result = await executeBrowserTool("browser.open", { url }, 7, "r_bad");
      expect(result.ok).toBe(false);
      expect(result.error).toMatch(/çalışamıyor|açılamaz/);
    }
    const missing = await executeBrowserTool("browser.open", {}, 7, "r_bad");
    expect(missing.error).toContain("url eksik");
    expect(chromeApi.create).not.toHaveBeenCalled();
    expect(taskTabOf("r_bad")).toBeUndefined();
  });

  it("new_tab:false loads the page in the current task tab instead", async () => {
    install([{ id: 7 }]);

    const result = await executeBrowserTool("browser.open", { url: "https://example.org/a", new_tab: false }, 7, "r_2");

    expect(result.ok).toBe(true);
    expect(result.data).toMatchObject({ url: "https://example.org/a", tab_id: 7, new_tab: false });
    expect(chromeApi.create).not.toHaveBeenCalled();
    expect(taskTabOf("r_2")).toBe(7);
  });

  it("works with no drivable tab at all (only our own panel is open)", async () => {
    install([{ id: 1, url: `${EXTENSION_ROOT}src/panel/index.html` }]);

    const result = await executeBrowserTool("browser.open", { url: "https://example.org/" }, undefined, "r_3");

    expect(result.ok).toBe(true);
    expect(taskTabOf("r_3")).toBe(100);
  });
});

describe("task tab tracking", () => {
  it("after browser.open, later calls of the SAME request go to the new tab", async () => {
    install([{ id: 7, url: "https://example.com/" }]);

    await executeBrowserTool("browser.open", { url: "https://tr.wikipedia.org/" }, 7, "r_a");
    // The panel keeps passing the tab it captured at send time (7).
    const snap = await executeBrowserTool("browser.snapshot", {}, 7, "r_a");
    const read = await executeBrowserTool("browser.read", {}, 7, "r_a");

    expect(snap.ok && read.ok).toBe(true);
    expect(sentTo.map((m) => m.tabId)).toEqual([100, 100]);

    // Another request is not affected.
    await executeBrowserTool("browser.snapshot", {}, 7, "r_other");
    expect(sentTo.at(-1)?.tabId).toBe(7);
    // Nor is a caller that passes no task id (e.g. the panel's evidence chips).
    await executeBrowserTool("browser.snapshot", {}, 7);
    expect(sentTo.at(-1)?.tabId).toBe(7);
  });

  it("falls back to the request tab when the task tab was closed", async () => {
    install([{ id: 7 }]);
    await executeBrowserTool("browser.open", { url: "https://example.org/" }, 7, "r_b");
    tabs.delete(100);

    await executeBrowserTool("browser.snapshot", {}, 7, "r_b");

    expect(sentTo.at(-1)?.tabId).toBe(7);
    expect(taskTabOf("r_b")).toBeUndefined();
  });

  it("navigate updates the task tab and forgetTaskTab clears it", async () => {
    install([{ id: 7 }]);
    await executeBrowserTool("browser.open", { url: "https://a.example/" }, 7, "r_c");
    const nav = await executeBrowserTool("browser.navigate", { url: "https://b.example/" }, 7, "r_c");

    // navigate drove the NEW tab (the task tab), not the original one.
    expect(nav.ok).toBe(true);
    expect(tabs.get(100)?.url).toBe("https://b.example/");
    expect(tabs.get(7)?.url).toBe("https://example.com/");
    expect(taskTabOf("r_c")).toBe(100);

    forgetTaskTab("r_c");
    expect(taskTabOf("r_c")).toBeUndefined();
  });
});

describe("navigate from a restricted / blank tab", () => {
  it("navigates the new-tab page itself when Chrome allows it", async () => {
    install([{ id: 9, url: "brave://newtab/" }]);

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.org/" }, 9, "r_n");

    expect(result.ok).toBe(true);
    expect(tabs.get(9)?.url).toBe("https://example.org/");
    expect(chromeApi.create).not.toHaveBeenCalled();
    expect(taskTabOf("r_n")).toBe(9);
  });

  it("falls back to opening a new tab instead of failing when the page refuses", async () => {
    install([{ id: 9, url: "chrome://newtab/" }]);
    refuseUpdateOn = (tab) => tab.url.startsWith("chrome://");

    const result = await executeBrowserTool("browser.navigate", { url: "https://example.org/" }, 9, "r_f");

    expect(result.ok).toBe(true);
    expect(result.data).toMatchObject({ url: "https://example.org/", tab_id: 100, new_tab: true });
    expect(taskTabOf("r_f")).toBe(100);
    // Subsequent page tools follow the new tab.
    await executeBrowserTool("browser.snapshot", {}, 9, "r_f");
    expect(sentTo.at(-1)?.tabId).toBe(100);
  });

  it("a page tool on a blank tab tells the model to use browser.open", async () => {
    install([{ id: 9, url: "about:blank" }]);

    const result = await executeBrowserTool("browser.snapshot", {}, 9, "r_s");

    expect(result.ok).toBe(false);
    expect(result.error).toContain("browser.open");
  });
});
