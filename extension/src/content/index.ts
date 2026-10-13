/**
 * Content script entry point: receives `{kind:"tool"}` messages from the
 * bridge and routes them to snapshot/actions.
 *
 * Guarded against double execution because the bridge may inject this file
 * into a tab that already had it from the manifest.
 */

import {
  type PingMessage,
  type ToolMessage,
  type ToolResponse,
  fail,
  ok,
} from "./types";
import * as actions from "./actions";
import { takeSnapshot } from "./snapshot";

declare global {
  interface Window {
    __aiAssistantContentReady?: boolean;
  }
}

const LOG = "[content]";

/**
 * `kind: "tool"` is ours alone (the service worker uses `kind: "bg"`), so we
 * claim such a message even when the rest of it is malformed — the contract
 * requires an envelope back, never a silently closed port.
 */
function isToolKind(value: unknown): value is { kind: "tool"; name?: unknown; args?: unknown } {
  return (
    typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "tool"
  );
}

function isPing(value: unknown): value is PingMessage {
  return (
    typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "ping"
  );
}

/** Handlers promise `Record<string, unknown>`; the wire promises nothing. */
function toArgs(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

async function dispatch(message: ToolMessage): Promise<ToolResponse> {
  const args = toArgs(message.args);
  switch (message.name) {
    case "browser.snapshot":
      return ok(takeSnapshot());
    case "browser.click":
      return actions.click(args);
    case "browser.type":
      return actions.type(args);
    case "browser.scroll":
      return actions.scroll(args);
    case "browser.read":
      return actions.read(args);
    case "browser.highlight":
      return actions.highlight(args);
    case "browser.navigate":
      // Handled in the bridge via chrome.tabs; never reaches the page.
      return fail("browser.navigate content script'te çalışmaz");
    default:
      return fail(`Bilinmeyen tool: ${message.name}`);
  }
}

if (window.__aiAssistantContentReady !== true) {
  window.__aiAssistantContentReady = true;

  chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
    if (isPing(message)) {
      sendResponse(ok({ ready: true }));
      return true;
    }
    if (!isToolKind(message)) {
      return false; // not ours (e.g. a kind:"bg" message): let others answer
    }
    if (typeof message.name !== "string") {
      sendResponse(fail("tool adı eksik ya da metin değil"));
      return false;
    }

    const name = message.name;
    dispatch({ kind: "tool", name, args: message.args }).then(sendResponse, (error: unknown) => {
      console.warn(LOG, name, error);
      sendResponse(fail(error instanceof Error ? error.message : String(error)));
    });
    return true; // keep the message channel open for the async reply
  });

  console.info(LOG, "hazır", window.location.href);
}
