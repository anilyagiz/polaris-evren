/**
 * Where is the panel running?
 *
 * - "extension": Brave side panel (chrome.runtime.id exists) — full browser tools.
 * - "desktop":   Polaris desktop window (tray / pywebview / WebView2) loading the
 *                panel same-origin from the backend at `/app/`. No extension
 *                APIs: browser.* tool calls are refused with a Turkish message and
 *                the backend is simply `window.location.origin`.
 */

export type RuntimeKind = "extension" | "desktop";

/** Path the backend serves the built panel under (backend/desktop_app.py). */
export const DESKTOP_APP_PREFIX = "/app/";

export const DESKTOP_BROWSER_ERROR =
  "Bu işlem tarayıcıda yapılır: Brave'de Polaris panelini aç.";

interface RuntimeEnv {
  chrome?: unknown;
  location?: { protocol: string; pathname: string; origin: string };
}

function currentEnv(): RuntimeEnv {
  return {
    chrome: (globalThis as { chrome?: unknown }).chrome,
    location: typeof window !== "undefined" ? window.location : undefined,
  };
}

function hasExtensionRuntime(chromeValue: unknown): boolean {
  try {
    const runtime = (chromeValue as { runtime?: { id?: unknown } } | undefined)
      ?.runtime;
    return typeof runtime?.id === "string" && runtime.id.length > 0;
  } catch {
    return false;
  }
}

export function runtimeKind(env: RuntimeEnv = currentEnv()): RuntimeKind {
  if (hasExtensionRuntime(env.chrome)) return "extension";
  const loc = env.location;
  // Only the page served by the backend at /app/ is the desktop window; the
  // vite dev page and the test DOM stay "extension" (their tests stub chrome).
  if (
    loc &&
    /^https?:$/.test(loc.protocol) &&
    loc.pathname.startsWith(DESKTOP_APP_PREFIX)
  )
    return "desktop";
  return "extension";
}

export function isDesktopMode(env?: RuntimeEnv): boolean {
  return runtimeKind(env) === "desktop";
}

/** Same-origin backend of the desktop window, or null inside the extension. */
export function desktopBackend(env: RuntimeEnv = currentEnv()): string | null {
  return isDesktopMode(env) && env.location ? env.location.origin : null;
}

/** `hello.client` for the WebSocket handshake. */
export function helloClient(env?: RuntimeEnv): RuntimeKind {
  return runtimeKind(env);
}

/** tool_result for a browser.* call the desktop window cannot run. */
export function refuseBrowserTool(): {
  ok: false;
  data: null;
  error: string;
} {
  return { ok: false, data: null, error: DESKTOP_BROWSER_ERROR };
}

/** Welcome suggestions that make sense without a browser tab. */
export const DESKTOP_SUGGESTIONS = [
  {
    label: "Yarın 9’da hatırlat",
    text: "Yarın saat 9’da spor salonunu hatırlat",
    detail: "Gününü kolaylaştır",
    icon: "01",
  },
  {
    label: "Web’de araştır",
    text: "Bugün İstanbul’da hava nasıl?",
    detail: "Kaynaklarla yanıtla",
    icon: "02",
  },
  {
    label: "Bunu hatırla",
    text: "Kahvemi sütsüz içtiğimi hatırla",
    detail: "Kişisel hafızaya kaydet",
    icon: "03",
  },
  {
    label: "Hatırlatmalarım",
    text: "Hatırlatmalarımı listele",
    detail: "Yaklaşanları gör",
    icon: "04",
  },
];
