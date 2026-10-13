/**
 * MV3 service worker.
 *
 * Responsibilities (K2):
 *  - open the side panel when the toolbar icon is clicked,
 *  - fall back to a normal tab when Brave does not expose the sidePanel API,
 *  - expose tiny helpers the panel/bridge can call via `chrome.runtime.sendMessage`.
 *
 * Tool execution does NOT go through here: the panel calls
 * `executeBrowserTool()` directly (see src/bridge/executeTool.ts).
 */

const LOG = "[background]";

/** Panel page path, read from the manifest so K1 can move it freely. */
function panelPath(): string {
  const manifest = chrome.runtime.getManifest();
  const fromSidePanel = (manifest as { side_panel?: { default_path?: string } }).side_panel
    ?.default_path;
  const fromAction = manifest.action?.default_popup;
  return fromSidePanel ?? fromAction ?? "src/panel/index.html";
}

async function openPanelInTab(): Promise<void> {
  const url = chrome.runtime.getURL(panelPath());
  const existing = await chrome.tabs.query({ url });
  const first = existing[0];
  if (first?.id !== undefined) {
    await chrome.tabs.update(first.id, { active: true });
    await chrome.windows.update(first.windowId, { focused: true });
    return;
  }
  await chrome.tabs.create({ url });
}

/** True when the sidePanel API is present AND the behavior could be set. */
async function enableSidePanel(): Promise<boolean> {
  if (!chrome.sidePanel?.setPanelBehavior) {
    console.warn(LOG, "sidePanel API yok, sekme yedeğine düşülüyor");
    return false;
  }
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
    return true;
  } catch (error) {
    console.warn(LOG, "setPanelBehavior başarısız:", error);
    return false;
  }
}

/**
 * Open the real side panel for the clicked window.
 *
 * `chrome.sidePanel.open()` only works while a user gesture is live, which an
 * `action.onClicked` handler still has.
 */
async function openSidePanelNow(windowId: number): Promise<boolean> {
  if (chrome.sidePanel?.open === undefined || windowId === chrome.windows.WINDOW_ID_NONE) {
    return false;
  }
  try {
    await chrome.sidePanel.open({ windowId });
    return true;
  } catch (error) {
    console.warn(LOG, "sidePanel.open başarısız:", error);
    return false;
  }
}

/**
 * When `openPanelOnActionClick` is active the click never reaches us, so
 * reaching this listener means the side panel did NOT open by itself.
 *
 * Do not jump straight to the tab fallback: if a Chromium fork fires the click
 * anyway, the user ends up with the panel AND a full tab copy of it, i.e. two
 * panel instances each opening their own backend WebSocket session. Try the
 * real API once (the user gesture is still live) and only then fall back.
 *
 * Registering the listener unconditionally at the top level is required by MV3
 * (listeners must be added synchronously on worker startup).
 */
chrome.action.onClicked.addListener((tab) => {
  void (async () => {
    if (await openSidePanelNow(tab.windowId)) {
      return;
    }
    await openPanelInTab();
  })().catch((error: unknown) => {
    // Nothing else can report this: an unhandled rejection here is invisible.
    console.warn(LOG, "panel açılamadı:", error);
  });
});

chrome.runtime.onInstalled.addListener(() => {
  void enableSidePanel().then((enabled) => {
    console.info(LOG, enabled ? "side panel hazır" : "side panel yok, sekme kullanılacak");
  });
});

chrome.runtime.onStartup.addListener(() => {
  void enableSidePanel();
});

// The worker can be respawned without onInstalled/onStartup firing. The call is
// idempotent (Chrome persists the behavior), so repeating it on every wake is
// harmless, and being cut short by worker termination costs nothing.
void enableSidePanel();

const BACKGROUND_MESSAGE_NAMES = ["openPanelTab", "activeTab"] as const;

type BackgroundMessageName = (typeof BACKGROUND_MESSAGE_NAMES)[number];

interface BackgroundMessage {
  kind: "bg";
  name: BackgroundMessageName;
}

function isBackgroundMessage(value: unknown): value is BackgroundMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as { kind?: unknown; name?: unknown };
  // Check the name against the real union: a predicate that only tests
  // `typeof name === "string"` lies to every caller downstream.
  return (
    candidate.kind === "bg" &&
    (BACKGROUND_MESSAGE_NAMES as readonly unknown[]).includes(candidate.name)
  );
}

/** Our own pages must never be reported as "the tab the user is looking at". */
function isOwnPage(url: string | undefined): boolean {
  if (url === undefined || url === "") {
    return false;
  }
  return url.startsWith(chrome.runtime.getURL(""));
}

/**
 * Resolve the tab the user is actually on.
 *
 * `{active: true, lastFocusedWindow: true}` alone returns the panel's own tab
 * when the panel runs as a tab (the Brave fallback), and the devtools/popup
 * tab when such a window has focus.
 */
async function findActiveTab(): Promise<chrome.tabs.Tab | null> {
  const queries: chrome.tabs.QueryInfo[] = [
    { active: true, lastFocusedWindow: true, windowType: "normal" },
    { active: true, currentWindow: true, windowType: "normal" },
    { active: true, windowType: "normal" },
  ];
  for (const query of queries) {
    let tabs: chrome.tabs.Tab[] = [];
    try {
      tabs = await chrome.tabs.query(query);
    } catch {
      continue;
    }
    const match = tabs.find(
      (tab) => tab.id !== undefined && tab.id !== chrome.tabs.TAB_ID_NONE && !isOwnPage(tab.url),
    );
    if (match !== undefined) {
      return match;
    }
  }
  return null;
}

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (!isBackgroundMessage(message)) {
    return false;
  }

  if (message.name === "openPanelTab") {
    void openPanelInTab().then(
      () => sendResponse({ ok: true, data: null, error: null }),
      (error: unknown) => sendResponse({ ok: false, data: null, error: String(error) }),
    );
    return true;
  }

  if (message.name === "activeTab") {
    void findActiveTab().then(
      (tab) => sendResponse({ ok: true, data: tab, error: null }),
      (error: unknown) => sendResponse({ ok: false, data: null, error: String(error) }),
    );
    return true;
  }

  return false;
});
