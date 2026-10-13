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
    if (first.windowId !== undefined) {
      await chrome.windows.update(first.windowId, { focused: true });
    }
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
 * When `openPanelOnActionClick` is active the click never reaches us, so this
 * listener only fires on the fallback path. Registering it unconditionally at
 * the top level is required by MV3 (listeners must be sync on worker startup).
 */
chrome.action.onClicked.addListener(() => {
  void openPanelInTab();
});

chrome.runtime.onInstalled.addListener(() => {
  void enableSidePanel().then((enabled) => {
    console.info(LOG, enabled ? "side panel hazır" : "side panel yok, sekme kullanılacak");
  });
});

chrome.runtime.onStartup.addListener(() => {
  void enableSidePanel();
});

// The worker can be respawned without onInstalled/onStartup firing.
void enableSidePanel();

interface BackgroundMessage {
  kind: "bg";
  name: "openPanelTab" | "activeTab";
}

function isBackgroundMessage(value: unknown): value is BackgroundMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { kind?: unknown }).kind === "bg" &&
    typeof (value as { name?: unknown }).name === "string"
  );
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
    void chrome.tabs
      .query({ active: true, lastFocusedWindow: true })
      .then((tabs) => sendResponse({ ok: true, data: tabs[0] ?? null, error: null }))
      .catch((error: unknown) => sendResponse({ ok: false, data: null, error: String(error) }));
    return true;
  }

  return false;
});
