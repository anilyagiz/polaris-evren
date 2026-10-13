/**
 * Shared types for the browser computer-use layer (K2).
 *
 * The Snapshot shape is part of the FROZEN contract (ORTAK BAĞLAM §7) and is
 * identical for `browser.*` and `desktop.*`. Do not change it without K3.
 */

/** Tool names the panel may pass to `executeBrowserTool`. */
export const BROWSER_TOOLS = [
  "browser.snapshot",
  "browser.click",
  "browser.type",
  "browser.scroll",
  "browser.navigate",
  "browser.read",
  "browser.highlight",
] as const;

export type BrowserToolName = (typeof BROWSER_TOOLS)[number];

/** `[x, y, width, height]` in viewport coordinates, rounded to integers. */
export type BBox = [number, number, number, number];

export interface SnapshotElement {
  ref: number;
  role: string;
  /** Accessible name, whitespace-collapsed, max 80 chars. */
  name: string;
  /** Current value for form controls; `"••••"` for password fields. */
  value: string | null;
  bbox: BBox;
}

export interface Snapshot {
  source: "browser" | "desktop";
  url: string | null;
  title: string | null;
  elements: SnapshotElement[];
  /** First ~1500 chars of the main readable text. */
  text_excerpt: string;
  /** Always null for the browser layer; desktop (K4) fills it. */
  shot: string | null;
}

export interface ClickResult {
  ok: true;
}

export interface TypeResult {
  ok: true;
  /** True when `submit: true` was requested and a submit was dispatched. */
  submitted: boolean;
}

export interface ScrollResult {
  ok: true;
  /** Vertical scroll offset after the action. */
  scroll_y: number;
}

export interface NavigateResult {
  ok: true;
  url: string;
  title: string;
}

export interface ReadResult {
  /** Max 4000 chars. */
  text: string;
}

export interface HighlightResult {
  ok: true;
}

/** Uniform envelope returned by every layer: bridge, content router, handlers. */
export interface ToolResponse<T = unknown> {
  ok: boolean;
  data: T | null;
  error: string | null;
}

/** Message sent from the extension page (panel/bridge) to the content script. */
export interface ToolMessage {
  kind: "tool";
  name: string;
  /**
   * Deliberately `unknown`: `chrome.tabs.sendMessage` carries whatever the
   * caller passed (including `null`, an array or a string), so the router
   * normalizes this to a plain object before any handler sees it.
   */
  args: unknown;
}

/** Content-script liveness probe used by the injection fallback. */
export interface PingMessage {
  kind: "ping";
}

/** Limits from the contract. */
export const MAX_ELEMENTS = 150;
export const MAX_NAME_LENGTH = 80;
export const MAX_TEXT_EXCERPT = 1500;
export const MAX_READ_LENGTH = 4000;

export function ok<T>(data: T): ToolResponse<T> {
  return { ok: true, data, error: null };
}

export function fail(error: string): ToolResponse<never> {
  return { ok: false, data: null, error };
}
