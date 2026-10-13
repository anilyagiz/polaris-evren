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
  type OpenResult,
  type PingMessage,
  type Snapshot,
  type ToolMessage,
  type ToolResponse,
} from "../content/types";

export type { ToolResponse, Snapshot };

/** The real implementation is in place (the hour-1 mock is gone). */
export const IS_MOCK = false;

/** Per-call budget; the backend gives up at 20 s, we fail much earlier. */
const CALL_TIMEOUT_MS = 8000;
/** Navigation is the documented exception: page loads are slow. */
const NAVIGATE_TIMEOUT_MS = 15000;
/**
 * The page-load wait must expire *before* the outer navigate budget, otherwise
 * the generic "timeout" wins and we lose the specific "page did not load"
 * message. 15 s stays the contract budget for the whole `browser.navigate`.
 */
const PAGE_LOAD_TIMEOUT_MS = NAVIGATE_TIMEOUT_MS - 1000;
/**
 * A freshly injected content script registers its listener asynchronously
 * (see `injectContentScript`), so we probe it instead of guessing.
 */
const INJECT_READY_TIMEOUT_MS = 3000;
const INJECT_POLL_MS = 50;
/** chrome.tabs.onUpdated can be missed; a slow poll is the safety net. */
const TAB_POLL_MS = 300;
/**
 * How long a tab may stay "complete" after a navigation was requested before
 * we accept that no new document is coming (same-document / fragment-only
 * navigation). Chrome flips `status` to "loading" within a few ms of the
 * request starting, so this is generous.
 */
const NAVIGATE_IDLE_GRACE_MS = 1200;

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

/** Hostnames (optionally with a path prefix) that never run a content script. */
const RESTRICTED_HOSTS = ["chrome.google.com/webstore", "chromewebstore.google.com"];

const RESTRICTED_MESSAGE = "Bu sayfada asistan çalışamıyor";

/**
 * A leading `scheme:` in the raw input, EXCEPT when what follows is a port
 * number — `localhost:3000` is a host:port, `javascript:/**\/alert(1)` is not.
 */
const SCHEME_PATTERN = /^([a-z][a-z0-9+.-]*):(?!\d)/i;

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

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(messageOf(error));
}

function delay(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    setTimeout(() => resolve(), ms);
  });
}

/** Tool args arrive from the backend over WS: never trust the shape. */
function asArgs(args: Record<string, unknown> | undefined): Record<string, unknown> {
  return typeof args === "object" && args !== null ? args : {};
}

export function isRestrictedUrl(url: string | undefined): boolean {
  if (url === undefined || url === "") {
    return false;
  }
  const lower = url.toLowerCase();
  if (RESTRICTED_SCHEMES.some((scheme) => lower.startsWith(scheme))) {
    return true;
  }
  // Match on host + path, not on the whole string: a query parameter that
  // happens to mention the web store must not lock the assistant out.
  let target = lower;
  try {
    const parsed = new URL(url);
    target = `${parsed.hostname}${parsed.pathname}`.toLowerCase();
  } catch {
    /* Not parseable (already handled above for schemes): fall back to raw. */
  }
  return RESTRICTED_HOSTS.some((host) => target.startsWith(host) || target.includes(`/${host}`));
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string, hint?: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const suffix = hint === undefined ? "" : ` (${hint})`;
    const timer = setTimeout(
      () => reject(new Error(`timeout: ${label} ${ms} ms içinde bitmedi${suffix}`)),
      ms,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(asError(error));
      },
    );
  });
}

/** Our own pages (side panel, dev tester) can never be the automation target. */
function isOwnPage(url: string | undefined): boolean {
  if (url === undefined || url === "") {
    return false;
  }
  return url.startsWith(chrome.runtime.getURL(""));
}

function isUsableTab(tab: chrome.tabs.Tab): boolean {
  return tab.id !== undefined && tab.id !== chrome.tabs.TAB_ID_NONE && !isOwnPage(tab.url);
}

async function resolveTab(tabId?: number): Promise<chrome.tabs.Tab> {
  if (tabId !== undefined) {
    const requested = await chrome.tabs.get(tabId);
    // On the tab fallback (Brave without sidePanel, or the panel opened as a
    // tab) the panel's own `{active, currentWindow}` query returns the PANEL
    // tab and passes its id here. Our own page can never be the automation
    // target, so resolve the user's real page instead of failing every call
    // with "Bu sayfada asistan çalışamıyor" (found by scripts/browser_e2e).
    if (!isOwnPage(requested.url)) {
      return requested;
    }
  }
  // The caller (side panel, or the panel/dev tester running as a normal tab on
  // the Brave fallback path) is itself a tab in the focused window, so a plain
  // {active, lastFocusedWindow} query can hand back OUR OWN extension page —
  // which is then rejected as a restricted URL. Devtools/popup windows have the
  // same problem, hence windowType: "normal".
  const queries: chrome.tabs.QueryInfo[] = [
    { active: true, lastFocusedWindow: true, windowType: "normal" },
    { active: true, currentWindow: true, windowType: "normal" },
    { active: true, windowType: "normal" },
  ];
  let fallback: chrome.tabs.Tab | undefined;
  for (const query of queries) {
    let tabs: chrome.tabs.Tab[] = [];
    try {
      tabs = await chrome.tabs.query(query);
    } catch {
      continue;
    }
    const usable = tabs.filter(isUsableTab);
    // Prefer a real web page, most recently used first (panel may live in
    // another window than the page the user is talking about).
    const web = usable
      .filter((tab) => /^https?:/i.test(tab.url ?? ""))
      .sort((x, y) => (y.lastAccessed ?? 0) - (x.lastAccessed ?? 0))[0];
    if (web !== undefined) {
      return web;
    }
    fallback ??= usable[0];
  }
  if (fallback !== undefined) {
    return fallback;
  }
  throw new Error("Sürülebilir sekme yok, kullanıcıdan bir sayfa açmasını iste");
}

/**
 * Chrome's own failures arrive as English sentences aimed at an extension
 * developer ("Tab is discarded", "Extension context invalidated"). They go
 * straight into a `tool_result` and from there into the model's context, where
 * they say nothing about what to do next. Each known family is rewritten into
 * one Turkish sentence that names the recovery step; anything unrecognised is
 * passed through unchanged rather than guessed at.
 */
const CHROME_ERROR_HINTS: readonly (readonly [RegExp, string])[] = [
  [
    /receiving end does not exist|could not establish connection/i,
    "Sayfaya bağlanılamadı, sekmeyi yenileyip tekrar dene",
  ],
  [/cannot access|must request permission|blocked a frame/i, RESTRICTED_MESSAGE],
  [/no tab with id|invalid tab id/i, "Sekme artık yok, yeni bir snapshot al"],
  [/tab (is|was) discarded/i, "Sekme uykuya alınmış, önce sekmeye geç"],
  [
    /extension context invalidated/i,
    "Eklenti yeniden yüklendi, sekmeyi yenilemeden işlem yapılamaz",
  ],
  [
    /frame (was removed|not found)|no frame with id/i,
    "Sayfanın o bölümü kayboldu, yeni bir snapshot al",
  ],
  [
    /message port closed/i,
    "Sayfa cevap vermeden kapandı (muhtemelen yönlendirme), yeni bir snapshot al",
  ],
];

function toActionableError(error: unknown): string {
  const raw = messageOf(error);
  for (const [pattern, hint] of CHROME_ERROR_HINTS) {
    if (pattern.test(raw)) {
      return hint;
    }
  }
  return raw;
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

/**
 * Wait until the content script answers a ping.
 *
 * REQUIRED after `injectContentScript`: CRXJS does not put the content script
 * in the manifest, it puts a *loader* there whose whole body is
 * `(async () => { await import(getURL("assets/index.ts-*.js")) })()`.
 * `executeScript()` resolves as soon as that synchronous IIFE returns, i.e.
 * well before the dynamically imported module runs
 * `chrome.runtime.onMessage.addListener`. Sending the tool message right away
 * therefore fails with "Receiving end does not exist" a second time.
 *
 * Bounded on purpose: `withTimeout` cannot cancel us, so an unbounded loop
 * would keep polling the tab forever after the call budget has expired.
 */
async function waitForContentScript(tabId: number, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  const ping: PingMessage = { kind: "ping" };
  for (;;) {
    try {
      await chrome.tabs.sendMessage(tabId, ping);
      return;
    } catch (error: unknown) {
      // Anything other than "not listening yet" means it will never listen.
      if (!isMissingReceiver(error) || Date.now() + INJECT_POLL_MS >= deadline) {
        throw asError(error);
      }
    }
    await delay(INJECT_POLL_MS);
  }
}

/**
 * `chrome.tabs.sendMessage` resolves with `undefined` when the receiver does
 * not call `sendResponse`, so the envelope has to be re-checked here or the
 * panel would forward `{ok: undefined}` to the backend.
 */
function asEnvelope(response: unknown): ToolResponse {
  if (typeof response !== "object" || response === null || !("ok" in response)) {
    return fail("Sayfadan geçersiz yanıt geldi, yeni bir snapshot al");
  }
  const candidate = response as ToolResponse;
  return {
    ok: candidate.ok === true,
    data: candidate.data ?? null,
    error: candidate.error ?? (candidate.ok === true ? null : "Bilinmeyen sayfa hatası"),
  };
}

async function sendToContentScript(
  tabId: number,
  name: string,
  args: Record<string, unknown>,
): Promise<ToolResponse> {
  const message: ToolMessage = { kind: "tool", name, args };
  try {
    return asEnvelope(await chrome.tabs.sendMessage(tabId, message));
  } catch (error: unknown) {
    if (!isMissingReceiver(error)) {
      throw error;
    }
    // Tab was open before the extension was installed/reloaded: inject once.
    await injectContentScript(tabId);
    await waitForContentScript(tabId, INJECT_READY_TIMEOUT_MS);
    return asEnvelope(await chrome.tabs.sendMessage(tabId, message));
  }
}

interface LoadWatcher {
  /** Resolves when the tab finished loading a new document. */
  readonly done: Promise<void>;
  /** Detach everything; safe to call after `done` settled. */
  cancel(): void;
}

/**
 * Watch a tab until it has loaded a NEW document.
 *
 * MUST be created BEFORE `chrome.tabs.update()` is called. A watcher created
 * afterwards sees the status of the document we are leaving: Chrome keeps the
 * old page (status "complete") until the new response headers arrive, so a
 * plain "resolve on complete" check returns the OLD url/title for any
 * navigation slower than the poll interval. `sawLoading` is what distinguishes
 * "the old page is still here" from "the new page is ready".
 */
function watchTabLoad(
  tabId: number,
  previousUrl: string | undefined,
  timeoutMs: number,
): LoadWatcher {
  let settled = false;
  let sawLoading = false;
  let settle: (error?: Error) => void = () => undefined;
  let poller: ReturnType<typeof setInterval> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const idleDeadline = Date.now() + NAVIGATE_IDLE_GRACE_MS;

  /** True once the tab can no longer be showing the page we navigated away from. */
  function isNewDocument(url: string | undefined): boolean {
    return (
      sawLoading ||
      previousUrl === undefined ||
      url !== previousUrl ||
      // Nothing ever started loading: same-document (fragment) navigation.
      Date.now() >= idleDeadline
    );
  }

  function onUpdated(
    updatedTabId: number,
    changeInfo: chrome.tabs.TabChangeInfo,
    tab: chrome.tabs.Tab,
  ): void {
    if (updatedTabId !== tabId) {
      return;
    }
    if (changeInfo.status === "loading") {
      sawLoading = true;
      return;
    }
    if (changeInfo.status === "complete" && isNewDocument(tab.url)) {
      settle();
    }
  }

  function onRemoved(removedTabId: number): void {
    if (removedTabId === tabId) {
      settle(new Error("Sekme kapatıldı"));
    }
  }

  const done = new Promise<void>((resolve, reject) => {
    settle = (error?: Error): void => {
      if (settled) {
        return;
      }
      settled = true;
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.tabs.onRemoved.removeListener(onRemoved);
      clearInterval(poller);
      clearTimeout(timer);
      if (error === undefined) {
        resolve();
      } else {
        reject(error);
      }
    };
  });

  poller = setInterval(() => {
    chrome.tabs.get(tabId).then(
      (tab) => {
        if (tab.status === "loading") {
          sawLoading = true;
          return;
        }
        if (tab.status === "complete" && isNewDocument(tab.url)) {
          settle();
        }
      },
      // The tab was closed or discarded mid-navigation.
      (error: unknown) => settle(asError(error)),
    );
  }, TAB_POLL_MS);

  timer = setTimeout(
    () => settle(new Error(`timeout: sayfa ${timeoutMs} ms içinde yüklenmedi`)),
    timeoutMs,
  );

  chrome.tabs.onUpdated.addListener(onUpdated);
  chrome.tabs.onRemoved.addListener(onRemoved);

  return {
    done,
    cancel(): void {
      // Mark the rejection handled: the caller is giving up, not awaiting.
      void done.catch(() => undefined);
      settle(new Error("sayfa yükleme izlemesi iptal edildi"));
    },
  };
}

/**
 * Normalise user/model input into an http(s) URL.
 *
 * `javascript:` and `data:` must never reach `chrome.tabs.update`. Relying on
 * the "https://" prefix making them unparseable is NOT enough:
 * `javascript:/**\/alert(1)` survives it and becomes `https://javascript/...`.
 * The scheme is therefore allow-listed explicitly, before any rewriting.
 */
function normalizeNavigateUrl(raw: string): { url: string } | { error: string } {
  const trimmed = raw.trim();
  const scheme = SCHEME_PATTERN.exec(trimmed)?.[1]?.toLowerCase();
  if (scheme !== undefined && scheme !== "http" && scheme !== "https") {
    return { error: `${RESTRICTED_MESSAGE}: "${scheme}:" adresleri açılamaz` };
  }

  let url: string;
  try {
    url = new URL(scheme === undefined ? `https://${trimmed}` : trimmed).toString();
  } catch {
    return { error: `geçersiz url: ${trimmed}` };
  }
  // Defence in depth: the parser can still produce a non-http(s) result.
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return { error: RESTRICTED_MESSAGE };
  }
  if (isRestrictedUrl(url)) {
    return { error: RESTRICTED_MESSAGE };
  }
  return { url };
}

function readTargetUrl(args: Record<string, unknown>): { url: string } | { error: string } {
  const raw = args.url;
  if (typeof raw !== "string" || raw.trim() === "") {
    return { error: "url eksik, http(s) ile başlayan bir adres ver" };
  }
  return normalizeNavigateUrl(raw);
}

/**
 * Point an existing tab at an already-normalized URL and wait for the load.
 * Shared by `browser.navigate` and `browser.open {new_tab:false}`.
 */
async function loadInTab(tabId: number, url: string): Promise<{ url: string; title: string }> {
  const previousUrl = (await chrome.tabs.get(tabId)).url;
  // Attach the watcher FIRST so the "loading" transition cannot be missed; the
  // old code slept 150 ms instead and resolved against the previous document.
  const watcher = watchTabLoad(tabId, previousUrl, PAGE_LOAD_TIMEOUT_MS);
  try {
    await chrome.tabs.update(tabId, { url });
  } catch (error: unknown) {
    watcher.cancel();
    throw error;
  }
  await watcher.done;

  // Report where we actually ended up: redirects are normal and the model
  // needs the final URL, not the requested one.
  const tab = await chrome.tabs.get(tabId);
  return { url: tab.url ?? url, title: tab.title ?? "" };
}

/** Bring the tab (and its window) in front of the user, best effort. */
async function focusTab(tab: chrome.tabs.Tab): Promise<void> {
  try {
    if (tab.id !== undefined && tab.active !== true) {
      await chrome.tabs.update(tab.id, { active: true });
    }
  } catch {
    /* Focus is cosmetic: never fail the tool over it. */
  }
  try {
    if (tab.windowId !== undefined && chrome.windows?.update !== undefined) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } catch {
    /* Same: a window that refuses focus still has the page loaded. */
  }
}

/**
 * Open `url` in a NEW tab next to `opener` (or in the last focused normal
 * window), focus it and wait until it has loaded. The caller makes the new tab
 * the request's task tab.
 */
async function openInNewTab(
  url: string,
  opener: chrome.tabs.Tab | undefined,
): Promise<ToolResponse<OpenResult>> {
  const properties: chrome.tabs.CreateProperties = { url, active: true };
  if (opener?.windowId !== undefined && opener.windowId !== chrome.windows?.WINDOW_ID_NONE) {
    properties.windowId = opener.windowId;
    if (typeof opener.index === "number") {
      properties.index = opener.index + 1;
    }
    if (opener.id !== undefined) {
      properties.openerTabId = opener.id;
    }
  }

  let created: chrome.tabs.Tab;
  try {
    created = await chrome.tabs.create(properties);
  } catch (error: unknown) {
    // The opener's window may be a popup/devtools window or gone: retry plain.
    if (properties.windowId === undefined) {
      throw error;
    }
    created = await chrome.tabs.create({ url, active: true });
  }
  if (created.id === undefined) {
    return fail("Yeni sekme açılamadı");
  }

  // A brand-new tab has no previous document: the first "complete" that is not
  // the initial blank page is ours (the idle grace covers the rest).
  const watcher = watchTabLoad(created.id, "", PAGE_LOAD_TIMEOUT_MS);
  await focusTab(created);
  await watcher.done;

  const tab = await chrome.tabs.get(created.id);
  return ok({
    ok: true,
    url: tab.url ?? url,
    title: tab.title ?? "",
    tab_id: created.id,
    new_tab: true,
  });
}

async function navigate(
  tab: chrome.tabs.Tab,
  args: Record<string, unknown>,
): Promise<ToolResponse<NavigateResult>> {
  const target = readTargetUrl(args);
  if ("error" in target) {
    return fail(target.error);
  }
  if (tab.id === undefined) {
    return openInNewTab(target.url, undefined);
  }

  try {
    const loaded = await loadInTab(tab.id, target.url);
    return ok({ ok: true, ...loaded });
  } catch (error: unknown) {
    // A restricted page (brave://newtab, chrome://…) may refuse to be
    // navigated by an extension. Opening the page next to it reaches the same
    // goal instead of failing the user's request.
    if (!isRestrictedUrl(tab.url)) {
      throw error;
    }
    return openInNewTab(target.url, tab);
  }
}

/** `browser.open {url, new_tab?=true}`. */
async function open(
  opener: chrome.tabs.Tab | undefined,
  args: Record<string, unknown>,
): Promise<ToolResponse<OpenResult>> {
  const target = readTargetUrl(args);
  if ("error" in target) {
    return fail(target.error);
  }
  const newTab = !(args.new_tab === false || args.new_tab === "false" || args.new_tab === 0);
  if (!newTab && opener?.id !== undefined) {
    const result = await navigate(opener, args);
    if (!result.ok || result.data === null) {
      return result as ToolResponse<never>;
    }
    const tabId = result.data.tab_id ?? opener.id;
    await focusTab({ ...opener, id: tabId } as chrome.tabs.Tab);
    return ok({ ...result.data, tab_id: tabId, new_tab: result.data.new_tab ?? false });
  }
  return openInNewTab(target.url, opener);
}

// ---------------------------------------------------------------------------
// Task tab tracking
// ---------------------------------------------------------------------------

/**
 * request_id → the tab that request is driving. `browser.open` (and a
 * `navigate` that had to fall back to a new tab) moves a request to another
 * tab; every later call of the SAME request must follow it, even though the
 * panel keeps passing the tab it captured when the user hit enter.
 */
const taskTabs = new Map<string, number>();
const MAX_TASK_TABS = 64;

function rememberTaskTab(taskId: string | undefined, tabId: number | undefined): void {
  if (taskId === undefined || taskId === "" || tabId === undefined) {
    return;
  }
  taskTabs.delete(taskId);
  taskTabs.set(taskId, tabId);
  while (taskTabs.size > MAX_TASK_TABS) {
    const oldest = taskTabs.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    taskTabs.delete(oldest);
  }
}

/** The tab a request is currently driving, if it moved off its original tab. */
export function taskTabOf(taskId: string): number | undefined {
  return taskTabs.get(taskId);
}

/** Forget a finished request's tab (optional: the map is bounded anyway). */
export function forgetTaskTab(taskId: string): void {
  taskTabs.delete(taskId);
}

/** Test helper. */
export function resetTaskTabs(): void {
  taskTabs.clear();
}

async function preferredTabId(taskId: string | undefined, tabId: number | undefined): Promise<number | undefined> {
  if (taskId === undefined) {
    return tabId;
  }
  const tracked = taskTabs.get(taskId);
  if (tracked === undefined) {
    return tabId;
  }
  try {
    const tab = await chrome.tabs.get(tracked);
    if (tab.id !== undefined) {
      return tab.id;
    }
  } catch {
    /* The user closed the task tab: fall back to the request's own tab. */
  }
  taskTabs.delete(taskId);
  return tabId;
}

/** New-tab / blank pages: the model should open a site, not read this one. */
function isBlankPage(url: string | undefined): boolean {
  const lower = (url ?? "").toLowerCase();
  return (
    lower === "" ||
    lower === "about:blank" ||
    /^(chrome|brave|edge):\/\/(newtab|new-tab-page)/.test(lower) ||
    lower.startsWith("chrome-search://local-ntp")
  );
}

const BLANK_PAGE_MESSAGE = `${RESTRICTED_MESSAGE}: sekme boş (yeni sekme). Bir site açmak için browser.open kullan`;

/**
 * Execute a `browser.*` tool against a tab.
 *
 * Never throws: every failure is reported as `{ ok: false, error }` so the
 * panel can forward it to the backend as a `tool_result` frame unchanged.
 *
 * @param name  Tool name, e.g. `"browser.click"`.
 * @param args  Tool arguments as defined in ORTAK BAĞLAM §7.
 * @param tabId Target tab; the active tab is used when omitted.
 * @param taskId The request id: once `browser.open`/`navigate` moved this
 *               request to another tab, later calls go there instead of `tabId`.
 */
export async function executeBrowserTool(
  name: string,
  args: Record<string, unknown>,
  tabId?: number,
  taskId?: string,
): Promise<ToolResponse> {
  if (!isBrowserTool(name)) {
    return fail(`Bilinmeyen tool: ${name}`);
  }
  const callArgs = asArgs(args);

  try {
    const wanted = await preferredTabId(taskId, tabId);

    // Opening never needs a usable current tab: with none at all the page
    // still opens in the last focused window.
    if (name === "browser.open") {
      const opener = await resolveTab(wanted).catch(() => undefined);
      const result = await withTimeout(open(opener, callArgs), NAVIGATE_TIMEOUT_MS, name);
      if (result.ok && result.data !== null) {
        rememberTaskTab(taskId, result.data.tab_id);
      }
      return result;
    }

    let tab: chrome.tabs.Tab;
    try {
      tab = await resolveTab(wanted);
    } catch (error: unknown) {
      if (name !== "browser.navigate") {
        throw error;
      }
      // No drivable tab at all: navigating means "show me this page".
      const result = await withTimeout(navigate({} as chrome.tabs.Tab, callArgs), NAVIGATE_TIMEOUT_MS, name);
      if (result.ok && result.data !== null) {
        rememberTaskTab(taskId, result.data.tab_id);
      }
      return result;
    }
    if (tab.id === undefined) {
      return fail("Sürülebilir sekme yok, kullanıcıdan bir sayfa açmasını iste");
    }
    // Navigation is checked against its TARGET, not the current page: leaving a
    // brave://newtab is legitimate and is how most browser tasks start.
    if (name === "browser.navigate") {
      const result = await withTimeout(navigate(tab, callArgs), NAVIGATE_TIMEOUT_MS, name);
      if (result.ok && result.data !== null) {
        rememberTaskTab(taskId, result.data.tab_id ?? tab.id);
      }
      return result;
    }

    // Everything else needs a content script, which these pages never run.
    if (isRestrictedUrl(tab.url)) {
      return fail(isBlankPage(tab.url) ? BLANK_PAGE_MESSAGE : RESTRICTED_MESSAGE);
    }

    // The timeout does NOT cancel the content script: a `type`/`click` that ran
    // long may still land. Say so, so the model re-reads the page instead of
    // blindly repeating the action and submitting a form twice.
    return await withTimeout(
      sendToContentScript(tab.id, name, callArgs),
      CALL_TIMEOUT_MS,
      name,
      "işlem sayfada tamamlanmış olabilir, tekrar denemeden önce yeni bir snapshot al",
    );
  } catch (error: unknown) {
    return fail(toActionableError(error));
  }
}
