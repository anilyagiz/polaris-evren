import { normalizeBackend } from "./protocol";

export interface Settings {
  backend: string;
  speak: boolean;
}
export const DEFAULT_BACKEND = "http://localhost:8765";
export const defaultSettings: Settings = {
  backend: DEFAULT_BACKEND,
  speak: true,
};
const key = "polaris-panel-settings";

export async function loadSettings(): Promise<Settings> {
  try {
    const stored: unknown =
      typeof chrome !== "undefined" && chrome.storage?.local
        ? (await chrome.storage.local.get(key))[key]
        : (JSON.parse(localStorage.getItem(key) ?? "null") as unknown);
    if (typeof stored !== "object" || stored === null) return defaultSettings;
    const value = stored as Record<string, unknown>;
    let backend = defaultSettings.backend;
    if (typeof value.backend === "string") {
      try {
        backend = normalizeBackend(value.backend);
      } catch {
        backend = defaultSettings.backend;
      }
    }
    return {
      backend,
      speak:
        typeof value.speak === "boolean" ? value.speak : defaultSettings.speak,
    };
  } catch {
    return defaultSettings;
  }
}
export async function saveSettings(settings: Settings): Promise<void> {
  // Only known keys are written; the old `mock` flag from earlier builds is dropped.
  const stored: Settings = { backend: settings.backend, speak: settings.speak };
  if (typeof chrome !== "undefined" && chrome.storage?.local)
    await chrome.storage.local.set({ [key]: stored });
  else localStorage.setItem(key, JSON.stringify(stored));
}

/** Turkish validation message for a backend URL, or null when it is usable. */
export function backendError(value: string): string | null {
  if (!value.trim()) return "Backend adresi boş olamaz.";
  try {
    normalizeBackend(value);
    return null;
  } catch {
    return "Geçerli bir http:// veya https:// adresi yaz (örn. http://localhost:8765).";
  }
}
/** The manifest CSP only allows localhost / 127.0.0.1 connections. */
export function isLocalBackend(value: string): boolean {
  try {
    const host = new URL(value.trim()).hostname;
    return host === "localhost" || host === "127.0.0.1";
  } catch {
    return false;
  }
}
export function extensionVersion(): string {
  try {
    if (typeof chrome !== "undefined" && chrome.runtime?.getManifest)
      return chrome.runtime.getManifest().version;
  } catch {
    /* Outside the extension (tests, vite dev page). */
  }
  return "geliştirme";
}
