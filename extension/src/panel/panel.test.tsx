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
import { executeBrowserTool } from "virtual:panel-browser-tool";

vi.mock("virtual:panel-browser-tool", () => ({
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
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const mountApp = async () => {
  render(<App />);
  await act(async () => {
    await Promise.resolve();
  });
};

describe("Panel acceptance flows", () => {
  it("plays a complete mock session and highlights without touching a real tab", async () => {
    await mountApp();
    fireEvent.click(screen.getByRole("button", { name: /Bu sayfayı özetle/ }));
    await act(async () => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText("Sayfanın kısa özeti")).toBeTruthy();
    expect(screen.getByText("Cevap kontrol edildi")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Öğe #21" }));
    expect(screen.getByText(/Mock: öğe #21 seçildi/)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Görevi iptal et" }),
    ).toBeNull();
  });
  it("rejects a form and shows a final response only after the decision", async () => {
    await mountApp();
    fireEvent.click(screen.getByRole("button", { name: /Bu formu doldur/ }));
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByRole("button", { name: "Onayla" })).toBeTruthy();
    expect(screen.queryByText("Demo tamamlandı.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reddet" }));
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("Form gönderilmedi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Onayla" })).toBeNull();
  });
  it("cancels an active session and ignores all late mock steps", async () => {
    await mountApp();
    fireEvent.click(screen.getByRole("button", { name: /Bu sayfayı özetle/ }));
    fireEvent.click(screen.getByRole("button", { name: "Görevi iptal et" }));
    await act(async () => {
      vi.advanceTimersByTime(3500);
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
  localStorage.setItem(
    "polaris-panel-settings",
    JSON.stringify({
      backend: "http://localhost:8000",
      mock: false,
      speak: false,
    }),
  );
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
