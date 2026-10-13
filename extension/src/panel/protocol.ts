import { z } from "zod";

const args = z.record(z.unknown());
export const traceSchema = z.object({
  agent: z.enum([
    "router",
    "planner",
    "executor",
    "judge",
    "responder",
    "system",
  ]),
  decision: z.string(),
  why: z.string(),
  evidence: z.array(z.string()),
  confidence: z.number().min(0).max(1).nullable(),
  tool: z.object({ name: z.string(), args }).nullable(),
  scores: z
    .object({
      correct: z.number().int().min(1).max(5),
      complete: z.number().int().min(1).max(5),
      safe: z.number().int().min(1).max(5),
    })
    .nullable(),
  verdict: z.enum(["pass", "revise", "ask_user"]).nullable(),
  latency_ms: z.number().nonnegative(),
  tokens: z.number().nonnegative().nullable(),
  ts: z.string(),
});
export const serverEventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("trace"),
    request_id: z.string(),
    step: traceSchema,
  }),
  z.object({
    type: z.literal("tool_call"),
    call_id: z.string(),
    request_id: z.string(),
    name: z.string(),
    args,
  }),
  z.object({
    type: z.literal("need_confirm"),
    call_id: z.string(),
    request_id: z.string(),
    summary: z.string(),
    tool: z.string(),
    args,
  }),
  z.object({
    type: z.literal("final"),
    request_id: z.string(),
    answer: z.string(),
    spoken: z.string(),
    citations: z.array(
      z.object({
        title: z.string(),
        url: z.string().nullable().optional(),
        ref: z.number().int().nullable().optional(),
      }),
    ),
  }),
  z.object({ type: z.literal("notify"), text: z.string(), spoken: z.string() }),
  z.object({
    type: z.literal("error"),
    request_id: z.string().nullable(),
    message: z.string(),
  }),
]);
export type TraceStep = z.infer<typeof traceSchema>;
export type ServerEvent = z.infer<typeof serverEventSchema>;
export type Confirmation = Extract<ServerEvent, { type: "need_confirm" }>;
export type Citation = Extract<
  ServerEvent,
  { type: "final" }
>["citations"][number];
export type TabContext = { id: number; url: string; title: string };
export type ClientEvent =
  | { type: "hello"; client: "extension"; version: "0.1" }
  | {
      type: "user_msg";
      id: string;
      text: string;
      source: "text" | "voice";
      tab: TabContext | null;
    }
  | {
      type: "tool_result";
      call_id: string;
      ok: boolean;
      data: unknown | null;
      error: string | null;
    }
  | { type: "confirm"; call_id: string; approved: boolean }
  | { type: "cancel"; id: string };
export type ConnectionStatus = "connected" | "connecting" | "disconnected";

export function parseEvent(raw: string): ServerEvent {
  return serverEventSchema.parse(JSON.parse(raw) as unknown);
}
export function normalizeBackend(value: string): string {
  const url = new URL(value.trim());
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "Backend adresi http:// veya https:// ile başlamalı; kullanıcı bilgisi içermemeli.",
    );
  }
  return url.toString().replace(/\/+$/, "");
}
export function websocketUrl(backend: string): string {
  const url = new URL(`${normalizeBackend(backend)}/ws`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
export function safeUrl(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  try {
    const url = new URL(raw);
    return ["http:", "https:"].includes(url.protocol)
      ? url.toString()
      : undefined;
  } catch {
    return undefined;
  }
}
export function shotUrl(path: string, backend: string): string | undefined {
  if (!/^\/shots\/[a-zA-Z0-9_.-]+\.png$/.test(path)) return undefined;
  return `${backend}${path}`;
}
export function requestId(): string {
  const values = crypto.getRandomValues(new Uint8Array(6));
  return `r_${Array.from(values, (n) => "abcdefghijklmnopqrstuvwxyz0123456789"[n % 36]).join("")}`;
}
