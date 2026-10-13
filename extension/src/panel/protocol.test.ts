import { describe, expect, it } from "vitest";
import {
  normalizeBackend,
  parseEvent,
  parseSocketEvent,
  requestId,
  safeUrl,
  shotUrl,
  websocketUrl,
} from "./protocol";

describe("Protocol boundary", () => {
  it("normalizes omitted nullable trace fields without rejecting the step", () => {
    const event = parseEvent(
      JSON.stringify({
        type: "trace",
        request_id: "r_demo",
        step: {
          agent: "router",
          decision: "Route",
          why: "Reason",
          evidence: [],
          latency_ms: 100,
          ts: "2026-10-13T12:00:00+03:00",
        },
      }),
    );
    if (event.type !== "trace") throw new Error("Expected trace");
    expect(event.step).toMatchObject({
      confidence: null,
      tool: null,
      scores: null,
      verdict: null,
      tokens: null,
    });
    expect(() =>
      parseEvent(
        JSON.stringify({
          type: "trace",
          request_id: "r_demo",
          step: { ...event.step, confidence: 2 },
        }),
      ),
    ).toThrow();
  });
  it("ignores unknown socket event types while rejecting malformed known frames", () => {
    expect(parseSocketEvent(JSON.stringify({ type: "heartbeat" }))).toBeNull();
    expect(() => parseSocketEvent(JSON.stringify({ type: "trace" }))).toThrow();
    expect(() => parseSocketEvent("{")).toThrow();
  });
  it("accepts the frozen final frame including ref-only citations", () => {
    expect(
      parseEvent(
        JSON.stringify({
          type: "final",
          request_id: "r_abcdef",
          answer: "Cevap",
          spoken: "Cevap",
          citations: [{ title: "Öğe", ref: 21 }],
        }),
      ).type,
    ).toBe("final");
  });
  it("rejects malformed events instead of executing them", () => {
    expect(() => parseEvent("{")).toThrow();
    expect(() =>
      parseEvent(
        JSON.stringify({
          type: "tool_call",
          name: "browser.click",
          args: { ref: 7 },
        }),
      ),
    ).toThrow();
    expect(() =>
      parseEvent(JSON.stringify({ type: "other", message: "x" })),
    ).toThrow();
  });
  it("uses http/https with the exact ws endpoint and handles trailing slashes", () => {
    expect(normalizeBackend(" http://localhost:8000/ ")).toBe(
      "http://localhost:8000",
    );
    expect(websocketUrl("https://example.com/api/")).toBe(
      "wss://example.com/api/ws",
    );
  });
  it("rejects executable links, credentials and URL fragments in configuration", () => {
    expect(safeUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeUrl("data:text/html,test")).toBeUndefined();
    expect(() => normalizeBackend("https://key@example.com")).toThrow();
    expect(() => normalizeBackend("https://example.com/#x")).toThrow();
  });
  it("only loads evidence from the agreed screenshot endpoint", () => {
    expect(shotUrl("/shots/r_abcdef_2.png", "http://localhost:8000")).toBe(
      "http://localhost:8000/shots/r_abcdef_2.png",
    );
    expect(
      shotUrl("//evil.example/image.png", "http://localhost:8000"),
    ).toBeUndefined();
    expect(
      shotUrl("/shots/../secret.png", "http://localhost:8000"),
    ).toBeUndefined();
  });
  it("creates protocol-compatible IDs", () =>
    expect(requestId()).toMatch(/^r_[a-z0-9]{6}$/));
});
