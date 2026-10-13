// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DESKTOP_POLL_MS, DesktopSection } from "./DesktopSettings";
import { parseDesktopStatus } from "./desktop";

const status = (over: Record<string, unknown> = {}) => ({
  enabled: false,
  packages_installed: true,
  docker_installed: true,
  docker_running: true,
  image_present: true,
  image_name: "ghcr.io/trycua/linux:24.04",
  docker_url: "https://www.docker.com/products/docker-desktop/",
  installing: false,
  install_progress: null,
  last_error: null,
  ready: true,
  ...over,
});

type Reply = { status?: number; body: unknown };
function stub(handler: (method: string, path: string, body: Record<string, unknown>) => Reply) {
  const fn = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input);
    const body = init?.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : {};
    const reply = handler(init?.method ?? "GET", url.pathname, body);
    const code = reply.status ?? 200;
    return { ok: code >= 200 && code < 300, status: code, json: async () => reply.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const renderCard = () => render(<DesktopSection backend="http://localhost:8765" />);
const toggle = () => screen.getByRole("switch", { name: "Masaüstü kontrolü" }) as HTMLInputElement;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Masaüstü kontrolü card", () => {
  it("parses and clamps the status payload", () => {
    const parsed = parseDesktopStatus(
      status({ install_progress: { step: "image", message: "x", percent: 140 }, docker_url: "javascript:alert(1)" }),
    );
    expect(parsed?.install_progress?.percent).toBe(100);
    expect(parsed?.docker_url).toBe("https://www.docker.com/products/docker-desktop/");
    expect(parseDesktopStatus({})).toBeNull();
  });

  it("is hidden on an older server (404)", async () => {
    const fetchMock = stub(() => ({ status: 404, body: { detail: "Not Found" } }));
    const { container } = renderCard();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector(".desktop-section")).toBeNull());
  });

  it("ready + off: checklist all green, toggle turns it on live", async () => {
    let enabled = false;
    const fetchMock = stub((method, _path, body) => {
      if (method === "PUT") enabled = body.enabled === true;
      return { body: status({ enabled }) };
    });
    renderCard();
    await screen.findByRole("heading", { name: "Masaüstü kontrolü" });
    expect(screen.getByText(/izole bir Linux masaüstünde/)).toBeTruthy();
    const items = document.querySelectorAll(".desktop-check");
    expect([...items].map((li) => li.getAttribute("data-state"))).toEqual(["ok", "ok", "ok"]);
    expect(toggle().checked).toBe(false);
    expect(screen.queryByRole("button", { name: /Kur ve aç/ })).toBeNull();
    fireEvent.click(toggle());
    await waitFor(() => expect(toggle().checked).toBe(true));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(put![1]!.body as string)).toEqual({ enabled: true });
    expect(screen.getByText("Açık", { selector: ".status-chip" })).toBeTruthy();
  });

  it("Docker missing: download link + hint, no install button", async () => {
    stub(() => ({
      body: status({ docker_installed: false, docker_running: false, image_present: false, ready: false }),
    }));
    renderCard();
    const link = (await screen.findByRole("link", { name: /Docker Desktop’ı indir/ })) as HTMLAnchorElement;
    expect(link.href).toBe("https://www.docker.com/products/docker-desktop/");
    expect(link.target).toBe("_blank");
    expect(screen.queryByRole("button", { name: /Kur ve aç/ })).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText("Kurulu değil")).toBeTruthy();
  });

  it("Docker stopped: warns and offers a recheck", async () => {
    let running = false;
    const fetchMock = stub(() => ({
      body: status({ docker_running: running, image_present: running, ready: running }),
    }));
    renderCard();
    await screen.findByText(/Docker Desktop çalışmıyor/);
    running = true;
    fireEvent.click(screen.getByRole("button", { name: /Tekrar kontrol et/ }));
    await waitFor(() => expect(toggle().disabled).toBe(false));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("refresh=true"))).toBe(true);
  });

  it("Kur ve aç: starts the job, shows progress, polls every 2 s until done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let phase: "missing" | "installing" | "done" = "missing";
    const fetchMock = stub((method, path, body) => {
      if (method === "POST" && path === "/settings/desktop/install") {
        expect(body).toEqual({ enable: true });
        phase = "installing";
      }
      if (phase === "missing")
        return { body: status({ packages_installed: false, image_present: false, ready: false }) };
      if (phase === "installing")
        return {
          body: status({
            packages_installed: true,
            image_present: false,
            ready: false,
            installing: true,
            install_progress: { step: "image", message: "Masaüstü imajı indiriliyor (~1,3 GB)… %40", percent: 40 },
          }),
        };
      return { body: status({ enabled: true }) };
    });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: /Kur ve aç/ }));
    await screen.findByText(/%40/);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("40");
    expect(document.querySelector('.desktop-check[data-state="busy"] .desktop-check-label')?.textContent).toBe(
      "Masaüstü imajı",
    );
    const gets = () => fetchMock.mock.calls.filter(([, init]) => (init?.method ?? "GET") === "GET").length;
    const before = gets();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DESKTOP_POLL_MS);
    });
    expect(gets()).toBe(before + 1);
    phase = "done";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DESKTOP_POLL_MS);
    });
    await waitFor(() => expect(toggle().checked).toBe(true));
    expect(screen.queryByRole("progressbar")).toBeNull();
    const after = gets();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DESKTOP_POLL_MS * 3);
    });
    expect(gets()).toBe(after); // polling stops once the job is over
  });

  it("shows the job's Turkish error and the server's 409 reason", async () => {
    stub((method) =>
      method === "PUT"
        ? { status: 409, body: { detail: "Docker Desktop çalışmıyor, açıp tekrar dene." } }
        : { body: status({ last_error: "Python paketleri kurulamadı. İnternet bağlantını kontrol edip tekrar dene." }) },
    );
    renderCard();
    expect((await screen.findByRole("alert")).textContent).toContain("Python paketleri kurulamadı");
    fireEvent.click(toggle());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Docker Desktop çalışmıyor"));
    expect(toggle().checked).toBe(false);
  });
});
