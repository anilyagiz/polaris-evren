// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ServerEvent } from "./protocol";
import {
  DESKTOP_BROWSER_ERROR,
  desktopBackend,
  helloClient,
  isDesktopMode,
  refuseBrowserTool,
  runtimeKind,
} from "./runtime";
import { loadSettings } from "./settings";
import { executeBrowserTool } from "../bridge/executeTool";

vi.mock("../bridge/executeTool", () => ({
  executeBrowserTool: vi.fn(async () => ({ ok: true, data: {}, error: null })),
  taskTabOf: vi.fn(() => undefined),
}));

const loc = (pathname: string, origin = "http://127.0.0.1:8766") => ({
  protocol: origin.split("//")[0],
  pathname,
  origin,
});

describe("runtime detection", () => {
  it("is the extension whenever chrome.runtime.id exists", () => {
    expect(
      runtimeKind({ chrome: { runtime: { id: "abc" } }, location: loc("/app/") }),
    ).toBe("extension");
    expect(
      runtimeKind({
        chrome: { runtime: { id: "abc" } },
        location: loc("/src/panel/index.html", "chrome-extension://abc"),
      }),
    ).toBe("extension");
  });
  it("is the desktop window when served by the backend at /app/", () => {
    const env = { chrome: { webview: {} }, location: loc("/app/") };
    expect(runtimeKind(env)).toBe("desktop");
    expect(isDesktopMode(env)).toBe(true);
    expect(helloClient(env)).toBe("desktop");
    expect(desktopBackend(env)).toBe("http://127.0.0.1:8766");
    expect(
      runtimeKind({ location: loc("/app/src/panel/index.html") }),
    ).toBe("desktop");
  });
  it("keeps dev pages and the test DOM in extension mode", () => {
    expect(runtimeKind({ location: loc("/src/panel/index.html") })).toBe(
      "extension",
    );
    expect(runtimeKind({ location: loc("/application") })).toBe("extension");
    expect(desktopBackend({ location: loc("/") })).toBeNull();
    expect(runtimeKind()).toBe("extension");
  });
  it("refuses browser tools with the Turkish message", () => {
    expect(refuseBrowserTool()).toEqual({
      ok: false,
      data: null,
      error: DESKTOP_BROWSER_ERROR,
    });
    expect(DESKTOP_BROWSER_ERROR).toContain("Brave'de Polaris panelini aç");
  });
});

class LiveSocket {
  static OPEN = 1;
  static current: LiveSocket;
  static urls: string[] = [];
  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(url: string) {
    LiveSocket.current = this;
    LiveSocket.urls.push(url);
  }
  send(raw: string) {
    this.sent.push(JSON.parse(raw) as Record<string, unknown>);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(frame: ServerEvent) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe("desktop window mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    LiveSocket.urls = [];
    window.history.pushState({}, "", "/app/");
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("speechSynthesis", { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ ok: true, service: "polaris", llm_model: "deepseek-chat", llm_configured: true }),
      })),
    );
    vi.stubGlobal("WebSocket", LiveSocket);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("uses the page origin as backend, ignoring stored settings", async () => {
    localStorage.setItem(
      "polaris-panel-settings",
      JSON.stringify({ backend: "http://localhost:9999", speak: false }),
    );
    const settings = await loadSettings();
    expect(settings.backend).toBe(window.location.origin);
    expect(settings.speak).toBe(false);
  });

  it("says hello as desktop, sends no tab and refuses browser tool calls", async () => {
    render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    act(() => LiveSocket.current.open());
    const socket = LiveSocket.current;
    expect(LiveSocket.urls.at(-1)).toContain(window.location.host);
    expect(socket.sent[0]).toMatchObject({ type: "hello", client: "desktop" });
    // Desktop suggestions: nothing that needs a browser tab.
    expect(screen.queryByText("Bu sayfayı özetle")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Mesajın" }), {
      target: { value: "YouTube'u aç" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Mesajı gönder" }));
      await Promise.resolve();
    });
    const user = socket.sent.find((frame) => frame.type === "user_msg")!;
    expect(user.tab).toBeNull();
    await act(async () => {
      socket.receive({
        type: "tool_call",
        request_id: user.id as string,
        call_id: "c_open",
        name: "browser.open",
        args: { url: "https://youtube.com" },
      });
      await Promise.resolve();
    });
    expect(executeBrowserTool).not.toHaveBeenCalled();
    expect(socket.sent.find((frame) => frame.type === "tool_result")).toEqual({
      type: "tool_result",
      call_id: "c_open",
      ok: false,
      data: null,
      error: DESKTOP_BROWSER_ERROR,
    });
  });
});
