// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { loadSettings, saveSettings } from "./settings";
afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});
it("enables voice responses on a fresh install", async () => {
  localStorage.clear();
  expect((await loadSettings()).speak).toBe(true);
});
it("preserves a user's explicit muted preference", async () => {
  await saveSettings({
    backend: "http://localhost:8000",
    mock: true,
    speak: false,
  });
  expect((await loadSettings()).speak).toBe(false);
});
