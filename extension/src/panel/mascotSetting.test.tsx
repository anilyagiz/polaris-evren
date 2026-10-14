import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MascotSetting } from "./MascotSetting";

afterEach(() => vi.unstubAllGlobals());

it("loads the switch and saves a change", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ enabled: false }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ enabled: true }) });
  vi.stubGlobal("fetch", fetchMock);
  render(<MascotSetting backend="http://localhost:8765" />);
  const toggle = (await screen.findByRole("switch", { name: "Masaüstü maskotu" })) as HTMLInputElement;
  expect(toggle.checked).toBe(false);
  await act(async () => {
    fireEvent.click(toggle);
  });
  expect(fetchMock.mock.calls[1][0]).toBe("http://localhost:8765/settings/mascot");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ enabled: true });
  expect(toggle.checked).toBe(true);
});

it("hides itself when the server has no mascot endpoint", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
  const { container } = render(<MascotSetting backend="http://localhost:8765" />);
  await act(async () => {});
  expect(container.innerHTML).toBe("");
});
