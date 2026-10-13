import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectSocket } from "./ws";

class FakeSocket {
  static OPEN = 1;
  static sockets: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.sockets.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}
beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.sockets = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("WebSocket lifecycle", () => {
  it("sends hello first, sends user frames only when connected", () => {
    const client = connectSocket(
      "http://localhost:8000",
      vi.fn(),
      vi.fn(),
      vi.fn(),
    );
    const frame = { type: "cancel" as const, id: "r_abcdef" };
    expect(client.send(frame)).toBe(false);
    const socket = FakeSocket.sockets[0];
    socket.open();
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "hello",
      client: "extension",
      version: "0.1",
    });
    expect(client.send(frame)).toBe(true);
    expect(JSON.parse(socket.sent[1])).toEqual(frame);
    client.close();
  });
  it("reconnects at 1, 2, 4, 8, 10 seconds and does not replay actions", () => {
    const client = connectSocket(
      "http://localhost:8000",
      vi.fn(),
      vi.fn(),
      vi.fn(),
    );
    FakeSocket.sockets[0].open();
    client.send({ type: "cancel", id: "r_abcdef" });
    for (const delay of [1000, 2000, 4000, 8000, 10000, 10000]) {
      const old = FakeSocket.sockets.at(-1)!;
      const count = FakeSocket.sockets.length;
      old.close();
      vi.advanceTimersByTime(delay - 1);
      expect(FakeSocket.sockets.length).toBe(count);
      vi.advanceTimersByTime(1);
      expect(FakeSocket.sockets.length).toBe(count + 1);
    }
    FakeSocket.sockets.at(-1)!.open();
    expect(FakeSocket.sockets.at(-1)!.sent).toHaveLength(1);
    client.close();
    vi.advanceTimersByTime(30000);
    expect(FakeSocket.sockets.length).toBe(7);
  });
  it("handles invalid events and stops callbacks after closing", () => {
    const onEvent = vi.fn();
    const onError = vi.fn();
    const client = connectSocket(
      "http://localhost:8000",
      onEvent,
      vi.fn(),
      onError,
    );
    const socket = FakeSocket.sockets[0];
    socket.open();
    socket.onmessage?.({ data: "not-json" });
    socket.onmessage?.({ data: new Uint8Array() });
    expect(onError).toHaveBeenCalledTimes(2);
    expect(onEvent).not.toHaveBeenCalled();
    socket.onmessage?.({
      data: JSON.stringify({ type: "notify", text: "Toplantı", spoken: "" }),
    });
    expect(onEvent).toHaveBeenCalledOnce();
    client.close();
    expect(socket.onmessage).toBeNull();
  });
});
