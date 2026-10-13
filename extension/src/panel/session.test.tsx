// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ServerEvent } from "./protocol";
import { parseSocketEvent } from "./protocol";
import {
  SESSION_KEY,
  fetchLatestSession,
  turnsToExchanges,
  validSessionId,
} from "./session";

vi.mock("../bridge/executeTool", () => ({
  executeBrowserTool: vi.fn(async () => ({ ok: true, data: {}, error: null })),
  taskTabOf: vi.fn(() => undefined),
}));

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

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(
    "polaris-panel-settings",
    JSON.stringify({ backend: "http://localhost:8000", speak: false }),
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  vi.stubGlobal("speechSynthesis", { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, llm_model: "x", llm_configured: true }),
    })),
  );
  vi.stubGlobal("WebSocket", LiveSocket);
  vi.stubGlobal("chrome", { tabs: { query: vi.fn(async () => []) } });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const mount = async () => {
  render(<App />);
  await act(async () => {
    await Promise.resolve();
  });
  act(() => LiveSocket.current.open());
  return LiveSocket.current;
};

const sessionEvent = (session_id: string, turns: Array<[string, string]>): ServerEvent =>
  ({
    type: "session",
    session_id,
    turns: turns.map(([role, text], i) => ({
      role,
      text,
      request_id: `r_${Math.floor(i / 2)}`,
      ts: "2026-10-13T10:00:00Z",
    })),
  }) as ServerEvent;

describe("conversation session", () => {
  it("sends the stored session_id in hello and restores the previous chat", async () => {
    localStorage.setItem(SESSION_KEY, "s_abc123");
    const socket = await mount();
    expect(socket.sent[0]).toMatchObject({ type: "hello", session_id: "s_abc123" });
    act(() =>
      socket.receive(
        sessionEvent("s_abc123", [
          ["user", "Ankara'nın nüfusu kaç?"],
          ["assistant", "Ankara'nın nüfusu yaklaşık **5,8 milyon**."],
          ["user", "peki İzmir'in?"],
          ["assistant", "İzmir'in nüfusu yaklaşık 4,5 milyon."],
        ]),
      ),
    );
    expect(screen.getByText("Ankara'nın nüfusu kaç?")).toBeTruthy();
    expect(screen.getByText("peki İzmir'in?")).toBeTruthy();
    expect(screen.getByText("İzmir'in nüfusu yaklaşık 4,5 milyon.")).toBeTruthy();
    // Markdown is rendered for restored answers.
    expect(screen.getByText("5,8 milyon").tagName).toBe("STRONG");
    // A reconnect to the same session does not duplicate the restored chat.
    act(() =>
      socket.receive(
        sessionEvent("s_abc123", [
          ["user", "Ankara'nın nüfusu kaç?"],
          ["assistant", "x"],
        ]),
      ),
    );
    expect(screen.getAllByText("Ankara'nın nüfusu kaç?")).toHaveLength(1);
  });

  it("omits session_id on first run and persists the id the server assigns", async () => {
    const socket = await mount();
    expect(socket.sent[0]).toMatchObject({ type: "hello" });
    expect(socket.sent[0]).not.toHaveProperty("session_id");
    act(() => socket.receive(sessionEvent("s_new1", [])));
    expect(localStorage.getItem(SESSION_KEY)).toBe("s_new1");
    expect(screen.getByText("Ne yapmak istersin?")).toBeTruthy();
  });

  it("Yeni sohbet sends new_session, clears the view and adopts the fresh id", async () => {
    localStorage.setItem(SESSION_KEY, "s_old");
    const socket = await mount();
    act(() => socket.receive(sessionEvent("s_old", [["user", "Merhaba"], ["assistant", "Selam!"]])));
    expect(screen.getByText("Selam!")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yeni sohbet" }));
    expect(socket.sent.at(-1)).toEqual({ type: "new_session" });
    expect(screen.queryByText("Selam!")).toBeNull();
    act(() => socket.receive(sessionEvent("s_fresh", [])));
    expect(localStorage.getItem(SESSION_KEY)).toBe("s_fresh");
    expect(screen.getByText("Ne yapmak istersin?")).toBeTruthy();
  });
});

describe("session helpers", () => {
  it("validates ids and pairs turns into exchanges", () => {
    expect(validSessionId("s_ab-12")).toBe(true);
    expect(validSessionId("bad id!")).toBe(false);
    expect(validSessionId("x".repeat(65))).toBe(false);
    const pairs = turnsToExchanges([
      { role: "assistant", text: "orphan", request_id: "" },
      { role: "user", text: "a", request_id: "r1" },
      { role: "assistant", text: "A", request_id: "r1" },
      { role: "user", text: "b", request_id: "r2" },
    ]);
    expect(pairs.map((p) => [p.text, p.answer])).toEqual([
      ["a", "A"],
      ["b", ""],
    ]);
  });

  it("parses the session event and tolerates missing turns", () => {
    const event = parseSocketEvent(JSON.stringify({ type: "session", session_id: "s_1" }));
    expect(event).toEqual({ type: "session", session_id: "s_1", turns: [] });
  });

  it("fetchLatestSession returns a valid id or null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ session_id: "s_latest" }) })),
    );
    expect(await fetchLatestSession("http://localhost:8765")).toBe("s_latest");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ session_id: null }) })),
    );
    expect(await fetchLatestSession("http://localhost:8765")).toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    expect(await fetchLatestSession("http://localhost:8765")).toBeNull();
  });
});
