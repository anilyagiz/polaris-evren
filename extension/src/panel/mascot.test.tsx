// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Mascot } from "./mascot";
import { CLICK_DELAY_MS, hitRegions, hostCall, mascotMood } from "./mascotState";
import type { ServerEvent } from "./protocol";

const idle = {
  confirming: false,
  recording: false,
  typing: false,
  busy: false,
  speaking: false,
};

describe("mascot mood mapping", () => {
  it("is idle by default", () => {
    expect(mascotMood(idle)).toBe("idle");
  });
  it("maps each activity to its mood", () => {
    expect(mascotMood({ ...idle, typing: true })).toBe("listening");
    expect(mascotMood({ ...idle, recording: true })).toBe("listening");
    expect(mascotMood({ ...idle, busy: true })).toBe("working");
    expect(mascotMood({ ...idle, speaking: true })).toBe("speaking");
    expect(mascotMood({ ...idle, confirming: true })).toBe("waiting");
  });
  it("prefers confirmation > listening > working > speaking", () => {
    const all = {
      confirming: true,
      recording: true,
      typing: true,
      busy: true,
      speaking: true,
    };
    expect(mascotMood(all)).toBe("waiting");
    expect(mascotMood({ ...all, confirming: false })).toBe("listening");
    expect(
      mascotMood({ ...all, confirming: false, recording: false, typing: false }),
    ).toBe("working");
    expect(mascotMood({ ...idle, busy: false, speaking: true })).toBe(
      "speaking",
    );
  });
});

describe("mascot hit regions", () => {
  it("puts a circle inside the star and a rect inside the bubble", () => {
    const pet = { left: 16, top: 20, width: 120, height: 114 };
    expect(hitRegions(pet, null)).toEqual([
      { kind: "circle", x: 62, y: 69, w: 29, h: 29 },
    ]);
    const regions = hitRegions(pet, { left: 12, top: 100, width: 336, height: 120 });
    expect(regions[1]).toEqual({ kind: "rect", x: 18, y: 106, w: 324, h: 108 });
    expect(hitRegions(null, null)).toEqual([]);
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
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(frame: ServerEvent) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("desktop mascot", () => {
  let api: Record<string, ReturnType<typeof vi.fn>>;
  beforeEach(() => {
    localStorage.clear();
    window.history.pushState({}, "", "/app/src/panel/mascot.html");
    api = {
      open_main: vi.fn(async () => true),
      set_expanded: vi.fn(async () => true),
      hide_mascot: vi.fn(async () => true),
    };
    (window as unknown as { pywebview: unknown }).pywebview = { api };
    vi.stubGlobal("speechSynthesis", {
      cancel: vi.fn(),
      speak: vi.fn(),
      getVoices: () => [],
    });
    vi.stubGlobal("WebSocket", LiveSocket);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    window.history.pushState({}, "", "/");
  });

  async function mount() {
    const view = render(<Mascot />);
    await flush();
    act(() => LiveSocket.current.open());
    return view;
  }
  function pet() {
    return document.querySelector(".mascot-pet") as HTMLElement;
  }
  function clickPet() {
    fireEvent.click(pet().querySelector("button")!);
    act(() => vi.advanceTimersByTime(CLICK_DELAY_MS + 10));
  }
  function mood() {
    return document.querySelector(".mascot")!.getAttribute("data-mood");
  }

  it("says hello as desktop; click opens the bubble, Enter sends a task, final shows", async () => {
    localStorage.setItem(
      "polaris-panel-settings",
      JSON.stringify({ backend: "http://x", speak: false }),
    );
    await mount();
    vi.useFakeTimers();
    const socket = LiveSocket.current;
    expect(socket.sent[0]).toMatchObject({ type: "hello", client: "desktop" });
    expect(mood()).toBe("idle");

    clickPet();
    const input = screen.getByRole("textbox", { name: "Polaris'e görev" });
    expect(input.getAttribute("placeholder")).toBe("Polaris'e bir görev ver…");
    expect(api.set_expanded).toHaveBeenLastCalledWith(true);

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Yarın 8'de koşu" } });
    expect(mood()).toBe("listening");
    fireEvent.submit(input.closest("form")!);

    const msg = socket.sent.find((frame) => frame.type === "user_msg")!;
    expect(msg).toMatchObject({
      text: "Yarın 8'de koşu",
      source: "text",
      tab: null,
    });
    expect(mood()).toBe("working");
    expect(screen.getByText("Üzerinde çalışıyorum…")).toBeTruthy();

    await act(async () => {
      socket.receive({
        type: "final",
        request_id: msg.id as string,
        answer: "Tamam, yarın 08:00'de hatırlatırım.",
        spoken: "Tamam.",
        citations: [],
      });
      await Promise.resolve();
    });
    expect(screen.getByText("Tamam, yarın 08:00'de hatırlatırım.")).toBeTruthy();
    expect(mood()).toBe("idle");

    // Auto-hides after ~12 s and collapses the window.
    act(() => vi.advanceTimersByTime(12500));
    expect(screen.queryByText("Tamam, yarın 08:00'de hatırlatırım.")).toBeNull();
    expect(api.set_expanded).toHaveBeenLastCalledWith(false);
  });

  it("need_confirm waits for Onayla/Reddet and answers with confirm", async () => {
    await mount();
    const socket = LiveSocket.current;
    await act(async () => {
      socket.receive({
        type: "need_confirm",
        call_id: "c_1",
        request_id: "r_1",
        summary: "Dosyayı silmek istiyor",
        tool: "desktop.click",
        args: {},
      });
      await Promise.resolve();
    });
    expect(mood()).toBe("waiting");
    expect(screen.getByText("Dosyayı silmek istiyor")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Onayla" }));
    expect(socket.sent.at(-1)).toEqual({
      type: "confirm",
      call_id: "c_1",
      approved: true,
    });
    expect(mood()).toBe("idle");

    await act(async () => {
      socket.receive({
        type: "need_confirm",
        call_id: "c_2",
        request_id: "r_1",
        summary: "Tekrar?",
        tool: "x",
        args: {},
      });
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: "Reddet" }));
    expect(socket.sent.at(-1)).toEqual({
      type: "confirm",
      call_id: "c_2",
      approved: false,
    });
  });

  it("reminders shake the star and fill the bubble; tool calls are refused", async () => {
    localStorage.setItem(
      "polaris-panel-settings",
      JSON.stringify({ backend: "http://x", speak: false }),
    );
    await mount();
    const socket = LiveSocket.current;
    await act(async () => {
      socket.receive({
        type: "notify",
        text: "Koşu zamanı!",
        spoken: "Koşu zamanı",
      });
      await Promise.resolve();
    });
    expect(screen.getByText("Koşu zamanı!")).toBeTruthy();
    expect(screen.getByText("Hatırlatma")).toBeTruthy();
    expect(pet().className).toContain("mascot-shake");

    await act(async () => {
      socket.receive({
        type: "tool_call",
        call_id: "c_b",
        request_id: "r_x",
        name: "browser.open",
        args: {},
      });
      await Promise.resolve();
    });
    expect(socket.sent.at(-1)).toMatchObject({
      type: "tool_result",
      call_id: "c_b",
      ok: false,
    });
  });

  it("double click opens the main window instead of the bubble", async () => {
    await mount();
    vi.useFakeTimers();
    const button = pet().querySelector("button")!;
    fireEvent.click(button);
    act(() => vi.advanceTimersByTime(120));
    fireEvent.click(button);
    act(() => vi.advanceTimersByTime(CLICK_DELAY_MS + 10));
    expect(api.open_main).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("host calls are safe outside pywebview", () => {
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    expect(hostCall("open_main")).toBe(false);
  });
});
