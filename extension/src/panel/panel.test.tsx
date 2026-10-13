// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ConfirmModal, Markdown } from "./components";
import { createMockTransport } from "./mocks/session";
import type { ServerEvent } from "./protocol";
import { executeBrowserTool } from "../bridge/executeTool";

vi.mock("../bridge/executeTool", () => ({
  executeBrowserTool: vi.fn(async () => ({
    ok: true,
    data: { ok: true },
    error: null,
  })),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.stubGlobal("speechSynthesis", {
    cancel: vi.fn(),
    speak: vi.fn(),
    getVoices: () => [],
  });
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      lang = "";
      voice = null;
      onend = null;
      onerror = null;
      constructor(public text: string) {}
    },
  );
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal(
    "fetch",
    healthFetch({ ok: true, llm_model: "deepseek-chat", llm_configured: true }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function healthFetch(body: unknown, ok = true) {
  return vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  }));
}
const storeSettings = () =>
  localStorage.setItem(
    "polaris-panel-settings",
    JSON.stringify({ backend: "http://localhost:8000", speak: false }),
  );
const stubTabs = () =>
  vi.stubGlobal("chrome", {
    tabs: {
      query: vi.fn(async () => [
        { id: 123, url: "https://example.com", title: "Example" },
      ]),
    },
  });
const mountApp = async () => {
  render(<App />);
  await act(async () => {
    await Promise.resolve();
  });
};
/** Scripted session (shared/fixtures) injected the way tests inject transports. */
const mountScripted = async () => {
  storeSettings();
  stubTabs();
  render(
    <App
      createTransport={(_backend, onEvent, onStatus) =>
        createMockTransport(onEvent, onStatus)
      }
    />,
  );
  await act(async () => {
    await Promise.resolve();
  });
};
const clickAndRun = async (name: RegExp | string, ms: number) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
    await Promise.resolve();
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

describe("Panel acceptance flows", () => {
  it("plays a complete scripted session and highlights evidence on the request's tab", async () => {
    await mountScripted();
    await clickAndRun(/Bu sayfayı özetle/, 3500);
    expect(screen.getByText("Sayfanın kısa özeti")).toBeTruthy();
    // The "Neden?" timeline folds away once the answer arrives; open it.
    expect(screen.queryByText("Cevap kontrol edildi")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Neden\?/ }));
      await Promise.resolve();
    });
    expect(screen.getByText("Cevap kontrol edildi")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Öğe #21" }));
      await Promise.resolve();
    });
    expect(executeBrowserTool).toHaveBeenCalledWith(
      "browser.highlight",
      { ref: 21 },
      123,
    );
    expect(
      screen.queryByRole("button", { name: "Görevi iptal et" }),
    ).toBeNull();
  });
  it("rejects a form and shows a final response only after the decision", async () => {
    await mountScripted();
    await clickAndRun(/Bu formu doldur/, 3000);
    expect(screen.getByRole("button", { name: "Onayla" })).toBeTruthy();
    expect(screen.queryByText("Demo tamamlandı.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reddet" }));
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("Form gönderilmedi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Onayla" })).toBeNull();
  });
  it("cancels an active session and ignores all late scripted steps", async () => {
    await mountScripted();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Bu sayfayı özetle/ }));
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: "Görevi iptal et" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });
    expect(screen.getByText("Görev iptal edildi.")).toBeTruthy();
    expect(screen.queryByText("Sayfanın kısa özeti")).toBeNull();
  });
  it("sends approved:false at the 60-second deadline", () => {
    const decide = vi.fn();
    render(
      <ConfirmModal
        deadline={Date.now() + 60000}
        onDecide={decide}
        pending={{
          type: "need_confirm",
          call_id: "c_demo",
          request_id: "r_demo",
          summary: "Gönderilsin mi?",
          tool: "browser.click",
          args: { ref: 52 },
        }}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(59750);
    });
    expect(decide).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(decide).toHaveBeenCalledWith(false);
  });
  it("never renders executable HTML or script URLs from model answers", () => {
    const { container } = render(
      <Markdown
        text={
          "<script>alert(1)</script>\n\n[x](javascript:alert)\n\n![image](https://tracker.example/pixel)\n\n[Source](https://example.com)"
        }
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(
      screen.getByRole("link", { name: "Source" }).getAttribute("rel"),
    ).toContain("noopener");
  });
});

describe("Mock confirmation protocol", () => {
  it("pauses before final and resumes only for the matching call ID", () => {
    const events: ServerEvent[] = [];
    const client = createMockTransport((event) => events.push(event), vi.fn());
    client.send({
      type: "user_msg",
      id: "r_demo",
      text: "Formu gönder",
      source: "text",
      tab: null,
    });
    vi.advanceTimersByTime(5000);
    expect(events.some((event) => event.type === "final")).toBe(false);
    const pending = events.find((event) => event.type === "need_confirm");
    if (!pending || pending.type !== "need_confirm")
      throw new Error("No confirmation");
    client.send({ type: "confirm", call_id: "wrong", approved: true });
    vi.advanceTimersByTime(1000);
    expect(events.some((event) => event.type === "final")).toBe(false);
    client.send({ type: "confirm", call_id: pending.call_id, approved: false });
    vi.advanceTimersByTime(1000);
    expect(events.at(-1)?.type).toBe("final");
    client.close();
  });
});

class LiveSocket {
  static OPEN = 1;
  static current: LiveSocket;
  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(_url: string) {
    LiveSocket.current = this;
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

const mountLive = async () => {
  storeSettings();
  vi.stubGlobal("WebSocket", LiveSocket);
  vi.stubGlobal("chrome", {
    tabs: {
      query: vi.fn(async () => [
        { id: 123, url: "https://example.com", title: "Example" },
      ]),
    },
  });
  await mountApp();
  act(() => LiveSocket.current.open());
  fireEvent.change(screen.getByRole("textbox", { name: "Mesajın" }), {
    target: { value: "Sayfayı oku" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Mesajı gönder" }));
    await Promise.resolve();
  });
  return LiveSocket.current;
};

describe("Live protocol wiring", () => {
  it("allows a reused call_id in a new request but still deduplicates within each request", async () => {
    const socket = await mountLive();
    const firstId = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    await act(async () => {
      socket.receive({
        type: "tool_call",
        request_id: firstId,
        call_id: "c_1",
        name: "browser.read",
        args: {},
      });
      await Promise.resolve();
    });
    act(() =>
      socket.receive({
        type: "final",
        request_id: firstId,
        answer: "Bitti",
        spoken: "",
        citations: [],
      }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Mesajın" }), {
      target: { value: "Tekrar oku" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Mesajı gönder" }));
      await Promise.resolve();
    });
    const secondId = socket.sent
      .filter((frame) => frame.type === "user_msg")
      .at(-1)!.id as string;
    expect(secondId).not.toBe(firstId);
    await act(async () => {
      const call: ServerEvent = {
        type: "tool_call",
        request_id: secondId,
        call_id: "c_1",
        name: "browser.read",
        args: {},
      };
      socket.receive(call);
      socket.receive(call);
      await Promise.resolve();
    });
    expect(executeBrowserTool).toHaveBeenCalledTimes(2);
    expect(
      socket.sent.filter((frame) => frame.type === "tool_result"),
    ).toHaveLength(2);
  });
  it("attaches the active tab, forwards browser tools once and sends their result", async () => {
    const socket = await mountLive();
    const user = socket.sent.find((frame) => frame.type === "user_msg")!;
    expect(user.tab).toEqual({
      id: 123,
      url: "https://example.com",
      title: "Example",
    });
    const call: ServerEvent = {
      type: "tool_call",
      request_id: user.id as string,
      call_id: "c_read",
      name: "browser.read",
      args: {},
    };
    await act(async () => {
      socket.receive(call);
      socket.receive(call);
      await Promise.resolve();
    });
    expect(executeBrowserTool).toHaveBeenCalledOnce();
    expect(executeBrowserTool).toHaveBeenCalledWith("browser.read", {}, 123);
    expect(socket.sent.find((frame) => frame.type === "tool_result")).toEqual({
      type: "tool_result",
      call_id: "c_read",
      ok: true,
      data: { ok: true },
      error: null,
    });
  });
  it("sends rejected confirmation and ignores tool calls arriving after cancellation", async () => {
    const socket = await mountLive();
    const id = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    act(() =>
      socket.receive({
        type: "need_confirm",
        request_id: id,
        call_id: "c_confirm",
        summary: "Form gönderilsin mi?",
        tool: "browser.click",
        args: { ref: 52 },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reddet" }));
    expect(socket.sent.find((frame) => frame.type === "confirm")).toEqual({
      type: "confirm",
      call_id: "c_confirm",
      approved: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Görevi iptal et" }));
    await act(async () => {
      socket.receive({
        type: "tool_call",
        request_id: id,
        call_id: "c_late",
        name: "browser.click",
        args: { ref: 52 },
      });
      await Promise.resolve();
    });
    expect(executeBrowserTool).not.toHaveBeenCalled();
    expect(
      socket.sent.some((frame) => frame.type === "cancel" && frame.id === id),
    ).toBe(true);
  });
});

describe("First run and health UX", () => {
  it("checks the backend on open and shows how to start it when it is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    vi.stubGlobal("WebSocket", LiveSocket);
    await mountApp();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8765/health",
      expect.anything(),
    );
    expect(screen.getByText("Polaris sunucusu çalışmıyor")).toBeTruthy();
    expect(screen.getByText("start-polaris.cmd")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Komutu kopyala" })).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: /Bu sayfayı özetle/,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.queryByText(/Demo/)).toBeNull();
    // The card clears on its own once the server comes up.
    vi.stubGlobal("fetch", healthFetch({ ok: true, llm_configured: true }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText("Polaris sunucusu çalışmıyor")).toBeNull();
  });
  it("explains a missing LLM key and shows model/CUA in the status popover", async () => {
    storeSettings();
    vi.stubGlobal(
      "fetch",
      healthFetch({
        ok: true,
        llm_model: "deepseek-chat",
        llm_configured: false,
        cua_enabled: true,
        stt_ready: true,
        future_field: { nested: 1 },
      }),
    );
    vi.stubGlobal("WebSocket", LiveSocket);
    await mountApp();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Yapay zekâ anahtarı ayarlı değil")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Bağlantı durumu/ }));
    const popover = screen.getByRole("dialog", { name: "Bağlantı durumu" });
    expect(popover.textContent).toContain("deepseek-chat");
    expect(popover.textContent).toContain("Masaüstü (CUA)Açık");
    expect(popover.textContent).toContain("Ses tanımaHazır");
    expect(popover.textContent).not.toContain("Brave arama");
  });
});

describe("Conversation UX", () => {
  it("shows LLM errors in friendly Turkish with the raw detail tucked away", async () => {
    const socket = await mountLive();
    const id = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    act(() =>
      socket.receive({
        type: "error",
        request_id: id,
        message:
          "LLM kimlik doğrulaması reddedildi; DEEPSEEK_API_KEY değerini kontrol et.",
      }),
    );
    expect(screen.getByText("Yapay zekâ anahtarı kabul edilmedi")).toBeTruthy();
    expect(screen.getByText("Ayrıntı")).toBeTruthy();
  });
  it("cancels the running task with Esc", async () => {
    const socket = await mountLive();
    const id = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(
      socket.sent.some((frame) => frame.type === "cancel" && frame.id === id),
    ).toBe(true);
    expect(screen.getByText("Görev iptal edildi.")).toBeTruthy();
  });
  it("marks failed tool steps in the timeline", async () => {
    const socket = await mountLive();
    const id = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    act(() =>
      socket.receive({
        type: "trace",
        request_id: id,
        step: {
          agent: "executor",
          decision: "Araç hatası: araç zaman aşımı: browser.read (20 sn)",
          why: "araç zaman aşımı: browser.read (20 sn)",
          evidence: [],
          confidence: null,
          tool: { name: "browser.read", args: {} },
          scores: null,
          verdict: null,
          latency_ms: 20000,
          tokens: null,
          ts: "2026-10-13T14:02:11+03:00",
        },
      }),
    );
    expect(screen.getByText("Araç başarısız")).toBeTruthy();
    expect(screen.getByText("1 araç hatası")).toBeTruthy();
  });
  it("copies an answer and clears the chat", async () => {
    const writeText = vi.fn(async (_value: string) => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const socket = await mountLive();
    const id = socket.sent.find((frame) => frame.type === "user_msg")!
      .id as string;
    act(() =>
      socket.receive({
        type: "final",
        request_id: id,
        answer: "Uzun **cevap**",
        spoken: "",
        citations: [],
      }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cevabı kopyala" }));
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledWith("Uzun **cevap**");
    fireEvent.click(screen.getByRole("button", { name: "Sohbeti temizle" }));
    expect(screen.queryByText("Sayfayı oku")).toBeNull();
    expect(screen.getByText("Ne yapmak istersin?")).toBeTruthy();
  });
});
