import { normalizeBackend } from "./protocol";

export interface Settings {
  backend: string;
  mock: boolean;
  speak: boolean;
}
export const defaultSettings: Settings = {
  backend: "http://localhost:8000",
  mock: true,
  speak: false,
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
    return {
      backend:
        typeof value.backend === "string"
          ? normalizeBackend(value.backend)
          : defaultSettings.backend,
      mock: typeof value.mock === "boolean" ? value.mock : defaultSettings.mock,
      speak:
        typeof value.speak === "boolean" ? value.speak : defaultSettings.speak,
    };
  } catch {
    return defaultSettings;
  }
}
export async function saveSettings(settings: Settings): Promise<void> {
  if (typeof chrome !== "undefined" && chrome.storage?.local)
    await chrome.storage.local.set({ [key]: settings });
  else localStorage.setItem(key, JSON.stringify(settings));
}
