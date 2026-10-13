/**
 * Panel → browser tool bridge. THE ONLY ENTRY POINT the panel (K1) calls.
 *
 * Responsibilities:
 *  - resolve the target tab (active tab when none is given),
 *  - refuse pages where a content script can never run,
 *  - run `browser.navigate` through chrome.tabs,
 *  - forward everything else to the content script, injecting it once if the
 *    tab was opened before the extension was installed,
 *  - bound every call in time and never throw.
 *
 * The returned envelope maps 1:1 to the `tool_result` WS frame (ORTAK BAĞLAM §6).
 */

import {
  BROWSER_TOOLS,
  type BrowserToolName,
  type NavigateResult,
  type Snapshot,
  type ToolResponse,
} from "../content/types";

export type { ToolResponse, Snapshot };

/** The real implementation is in place (the hour-1 mock is gone). */
export const IS_MOCK = false;

/** Per-call budget; the backend gives up at 20 s, we fail much earlier. */
const CALL_TIMEOUT_MS = 8000;
/** Navigation is the documented exception: page loads are slow. */
const NAVIGATE_TIMEOUT_MS = 15000;

const RESTRICTED_SCHEMES = [
  "chrome:",
  "brave:",
  "edge:",
  "about:",
  "chrome-extension:",
  "moz-extension:",
  "devtools:",
  "view-source:",
];

const RESTRICTED_HOSTS = ["chrome.google.com/webstore", "chromewebstore.google.com"];

const RESTRICTED_MESSAGE = "Bu sayfada asistan çalışamıyor";

function isBrowserTool(name: string): name is BrowserToolName {
  return (BROWSER_TOOLS as readonly string[]).includes(name);
}

function ok<T>(data: T): ToolResponse<T> {
  return { ok: true, data, error: null };
}

function fail(error: string): ToolResponse<never> {
  return { ok: false, data: null, error };
}

function messageOf(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  return String(error);
}

export function isRestrictedUrl(url: string | undefined): boolean {
  if (url === undefined || url === "") {
    return false;
  }
  const lower = url.toLowerCase();
  if (RESTRICTED_SCHEMES.some((scheme) => lower.startsWith(scheme))) {
    return true;
  }
  return RESTRICTED_HOSTS.some((host) => lower.includes(host));
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout: ${label} ${ms} ms içinde bitmedi`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(messageOf(error)));
      },
    );
  });
}

async function resolveTab(tabId?: number): Promise<chrome.tabs.Tab> {
  if (tabId !== undefined) {
    return chrome.tabs.get(tabId);
  }
  const byFocus = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const tab = byFocus[0] ?? (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  if (tab === undefined || tab.id === undefined) {
    throw new Error("Aktif sekme bulunamadı");
  }
  return tab;
}

/** A missing receiver is the one error we retry after injecting the script. */
function isMissingReceiver(error: unknown): boolean {
  const message = messageOf(error).toLowerCase();
  return (
    message.includes("receiving end does not exist") ||
    message.includes("could not establish connection")
  );
}

/** Inject the content script listed in the manifest (build-name agnostic). */
async function injectContentScript(tabId: number): Promise<void> {
  if (chrome.scripting?.executeScript === undefined) {
    throw new Error('"scripting" izni yok, content script enjekte edilemiyor');
  }
  const files = chrome.runtime.getManifest().content_scripts?.[0]?.js ?? [];
  if (files.length === 0) {
    throw new Error("manifest'te content script tanımlı değil");
  }
  await chrome.scripting.executeScript({ target: { tabId, allFrames: false }, files });
}

async function sendToContentScript(
  tabId: number,
  name: string,
  args: Record<string, unknown>,
): Promise<ToolResponse> {
  const message = { kind: "tool" as const, name, args };
  try {
    return (await chrome.tabs.sendMessage(tabId, message)) as ToolResponse;
  } catch (error: unknown) {
    if (!isMissingReceiver(error)) {
      throw error;
    }
    // Tab was open before the extension was installed/reloaded: inject once.
    await injectContentScript(tabId);
    return (await chrome.tabs.sendMessage(tabId, message)) as ToolResponse;
  }
}

function waitForTabComplete(tabId: number, timeoutMs: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let settled = false;

    const finish = (error?: Error): void => {
      if (settled) {
        return;
      }
      settled = true;
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearInterval(poller);
      clearTimeout(timer);
      if (error === undefined) {
        resolve();
      } else {
        reject(error);
      }
    };

    function onUpdated(updatedTabId: number, changeInfo: chrome.tabs.TabChangeInfo): void {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        finish();
      }
    }

    const poller = setInterval(() => {
      chrome.tabs.get(tabId).then(
        (tab) => {
          if (tab.status === "complete") {
            finish();
          }
        },
        (error: unknown) => finish(new Error(messageOf(error))),
      );
    }, 300);

    const timer = setTimeout(
      () => finish(new Error(`timeout: sayfa ${timeoutMs} ms içinde yüklenmedi`)),
      timeoutMs,
    );

    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

async function navigate(
  tabId: number,
  args: Record<string, unknown>,
): Promise<ToolResponse<NavigateResult>> {
  const raw = args.url;
  if (typeof raw !== "string" || raw.trim() === "") {
    return fail("url eksik");
  }

  let url: string;
  try {
    url = new URL(raw.includes("://") ? raw : `https://${raw}`).toString();
  } catch {
    return fail(`geçersiz url: ${raw}`);
  }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return fail(RESTRICTED_MESSAGE);
  }
  if (isRestrictedUrl(url)) {
    return fail(RESTRICTED_MESSAGE);
  }

  await chrome.tabs.update(tabId, { url });
  // The status flips to "loading" asynchronously; give it a tick first.
  await new Promise((resolve) => setTimeout(resolve, 150));
  await waitForTabComplete(tabId, NAVIGATE_TIMEOUT_MS);

  const tab = await chrome.tabs.get(tabId);
  return ok({ ok: true, url: tab.url ?? url, title: tab.title ?? "" });
}

/**
 * Execute a `browser.*` tool against a tab.
 *
 * Never throws: every failure is reported as `{ ok: false, error }` so the
 * panel can forward it to the backend as a `tool_result` frame unchanged.
 *
 * @param name  Tool name, e.g. `"browser.click"`.
 * @param args  Tool arguments as defined in ORTAK BAĞLAM §7.
 * @param tabId Target tab; the active tab is used when omitted.
 */
export async function executeBrowserTool(
  name: string,
  args: Record<string, unknown>,
  tabId?: number,
): Promise<ToolResponse> {
  if (!isBrowserTool(name)) {
    return fail(`Bilinmeyen tool: ${name}`);
  }

  try {
    const tab = await resolveTab(tabId);
    if (tab.id === undefined) {
      return fail("Aktif sekme bulunamadı");
    }
    if (isRestrictedUrl(tab.url)) {
      return fail(RESTRICTED_MESSAGE);
    }

    if (name === "browser.navigate") {
      return await withTimeout(navigate(tab.id, args), NAVIGATE_TIMEOUT_MS + 1000, name);
    }

    return await withTimeout(sendToContentScript(tab.id, name, args), CALL_TIMEOUT_MS, name);
  } catch (error: unknown) {
    const message = messageOf(error);
    if (isMissingReceiver(error)) {
      return fail("Sayfaya bağlanılamadı, sekmeyi yenileyip tekrar dene");
    }
    return fail(message);
  }
}
