/**
 * Content script entry point: receives `{kind:"tool"}` messages from the
 * bridge and routes them to snapshot/actions.
 *
 * Guarded against double execution because the bridge may inject this file
 * into a tab that already had it from the manifest.
 */

import { type ToolMessage, type ToolResponse, fail, ok } from "./types";
import * as actions from "./actions";
import { takeSnapshot } from "./snapshot";

declare global {
  interface Window {
    __aiAssistantContentReady?: boolean;
  }
}

const LOG = "[content]";

function isToolMessage(value: unknown): value is ToolMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { kind?: unknown }).kind === "tool" &&
    typeof (value as { name?: unknown }).name === "string"
  );
}

function isPing(value: unknown): boolean {
  return (
    typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "ping"
  );
}

async function dispatch(message: ToolMessage): Promise<ToolResponse> {
  const args = message.args ?? {};
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
    if (!isToolMessage(message)) {
      return false;
    }

    dispatch(message).then(sendResponse, (error: unknown) => {
      console.warn(LOG, message.name, error);
      sendResponse(fail(error instanceof Error ? error.message : String(error)));
    });
    return true; // keep the message channel open for the async reply
  });

  console.info(LOG, "hazır", window.location.href);
}
