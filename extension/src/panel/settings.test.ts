// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  backendError,
  defaultSettings,
  extensionVersion,
  isLocalBackend,
  loadSettings,
  saveSettings,
} from "./settings";
afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});
it("talks to the local backend on a fresh install", async () => {
  localStorage.clear();
  const settings = await loadSettings();
  expect(settings).toEqual(defaultSettings);
  expect(settings.backend).toBe("http://localhost:8765");
  expect("mock" in settings).toBe(false);
});
it("enables voice responses on a fresh install", async () => {
  localStorage.clear();
  expect((await loadSettings()).speak).toBe(true);
});
it("preserves a user's explicit muted preference", async () => {
  await saveSettings({ backend: "http://localhost:8000", speak: false });
  expect((await loadSettings()).speak).toBe(false);
});
it("ignores the demo flag stored by earlier builds", async () => {
  localStorage.setItem(
    "polaris-panel-settings",
    JSON.stringify({
      backend: "http://127.0.0.1:8000/",
      mock: true,
      speak: true,
    }),
  );
  const loaded = await loadSettings();
  expect(loaded).toEqual({ backend: "http://127.0.0.1:8000", speak: true });
  await saveSettings(loaded);
  expect(localStorage.getItem("polaris-panel-settings")).not.toContain("mock");
});
it("falls back to the default backend when the stored URL is invalid", async () => {
  localStorage.setItem(
    "polaris-panel-settings",
    JSON.stringify({ backend: "ftp://x", speak: true }),
  );
  expect((await loadSettings()).backend).toBe("http://localhost:8765");
});
it("validates backend URLs (http/https only)", () => {
  expect(backendError("http://localhost:8000")).toBeNull();
  expect(backendError("https://example.com")).toBeNull();
  expect(backendError("  ")).toMatch(/boş/);
  expect(backendError("ws://localhost:8000")).toMatch(/http/);
  expect(backendError("javascript:alert(1)")).toMatch(/http/);
  expect(backendError("http://user:pw@localhost:8000")).toMatch(/http/);
  expect(isLocalBackend("http://127.0.0.1:9000")).toBe(true);
  expect(isLocalBackend("https://example.com")).toBe(false);
});
it("reads the version from the manifest", () => {
  expect(extensionVersion()).toBe("geliştirme");
  vi.stubGlobal("chrome", {
    runtime: { getManifest: () => ({ version: "0.1.0" }) },
  });
  expect(extensionVersion()).toBe("0.1.0");
});
