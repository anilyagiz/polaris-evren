/**
 * Conversation session persistence (docs/PROTOCOL.md §6).
 *
 * The backend keeps the conversation history per `session_id`; the panel only
 * remembers which session it belongs to and sends it in `hello`:
 * - extension (Brave side panel): chrome.storage.local
 * - desktop window / dev page:    localStorage
 * On its first run the desktop window adopts the most recent session from
 * GET /session/latest so it continues the chat the user had in the panel.
 */
import type { SessionTurn } from "./protocol";

export const SESSION_KEY = "polaris-session-id";
const SESSION_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function validSessionId(value: unknown): value is string {
  return typeof value === "string" && SESSION_RE.test(value);
}

export function hasExtensionStorage(): boolean {
  try {
    return typeof chrome !== "undefined" && !!chrome.storage?.local;
  } catch {
    return false;
  }
}

/** localStorage read (desktop window, tests); null inside the extension. */
export function loadSessionIdSync(): string | null {
  if (hasExtensionStorage()) return null;
  try {
    const value = localStorage.getItem(SESSION_KEY);
    return validSessionId(value) ? value : null;
  } catch {
    return null;
  }
}

export async function loadSessionId(): Promise<string | null> {
  try {
    if (hasExtensionStorage()) {
      const value: unknown = (await chrome.storage.local.get(SESSION_KEY))[
        SESSION_KEY
      ];
      return validSessionId(value) ? value : null;
    }
  } catch {
    return null;
  }
  return loadSessionIdSync();
}

export async function saveSessionId(id: string): Promise<void> {
  if (!validSessionId(id)) return;
  try {
    if (hasExtensionStorage())
      await chrome.storage.local.set({ [SESSION_KEY]: id });
    else localStorage.setItem(SESSION_KEY, id);
  } catch {
    /* Storage blocked: the session simply is not restored next time. */
  }
}

/** Most recent backend session (desktop first run); null when none/unreachable. */
export async function fetchLatestSession(
  backend: string,
  timeoutMs = 2500,
): Promise<string | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${backend}/session/latest`, {
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { session_id?: unknown };
    return validSessionId(body.session_id) ? body.session_id : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export interface RestoredExchange {
  id: string;
  text: string;
  answer: string;
}

/** Pair stored turns into completed user→assistant exchanges (oldest first). */
export function turnsToExchanges(turns: SessionTurn[]): RestoredExchange[] {
  const out: RestoredExchange[] = [];
  turns.forEach((turn, index) => {
    if (turn.role === "user") {
      out.push({
        id: `h_${turn.request_id || "x"}_${index}`,
        text: turn.text,
        answer: "",
      });
      return;
    }
    const last = out.at(-1);
    // An assistant turn whose user turn fell outside the window is dropped.
    if (last && !last.answer) last.answer = turn.text;
  });
  return out;
}
