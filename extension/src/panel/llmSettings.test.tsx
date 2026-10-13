// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { SettingsModal } from "./components";
import { prettyModel } from "./llm";
import { defaultSettings } from "./settings";

const llmFixture = () => ({
  presets: [
    {
      id: "evren",
      label: "EVREN",
      base_url: "https://evren.example/v1",
      key_hint: "evr-…",
      models: [
        { id: "gemma-4-31b", label: "Gemma 4 31B", note: "Dengeli ve hızlı" },
        { id: "gemma-4-12b", label: "Gemma 4 12B", note: "En hızlı" },
      ],
      recommended: {
        model: "gemma-4-31b",
        fallback_model: "gemma-4-12b",
        thinking: "off",
      },
    },
    {
      id: "deepseek",
      label: "DeepSeek",
      base_url: "https://api.deepseek.com",
      key_hint: "sk-…",
      models: [
        { id: "deepseek-chat", label: "DeepSeek Chat", note: "Genel" },
        { id: "deepseek-reasoner", label: "DeepSeek Reasoner", note: "Derin" },
      ],
      recommended: {
        model: "deepseek-chat",
        fallback_model: null,
        thinking: "on",
      },
    },
    {
      id: "custom",
      label: "Özel",
      base_url: "",
      key_hint: "",
      models: [],
      recommended: null,
    },
  ],
  provider: "evren",
  base_url: "https://evren.example/v1",
  model: "gemma-4-31b",
  fallback_model: "gemma-4-12b",
  thinking: "off",
  api_key_set: true,
  api_key_hint: "…X45NMo",
  brave_key_set: false,
  llm_configured: true,
});

type Reply = { status?: number; body: unknown };
interface Routes {
  llm?: () => Reply;
  put?: (body: Record<string, unknown>) => Reply;
  test?: (body: Record<string, unknown>) => Reply;
  models?: (url: URL) => Reply;
  health?: () => Reply;
}
function router(routes: Routes) {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input);
    const method = init?.method ?? "GET";
    const body = init?.body
      ? (JSON.parse(init.body as string) as Record<string, unknown>)
      : {};
    let reply: Reply = { status: 404, body: { detail: "Not Found" } };
    if (url.pathname === "/health" && routes.health) reply = routes.health();
    else if (url.pathname === "/settings/llm" && method === "GET" && routes.llm)
      reply = routes.llm();
    else if (url.pathname === "/settings/llm" && method === "PUT" && routes.put)
      reply = routes.put(body);
    else if (url.pathname === "/settings/llm/test" && routes.test)
      reply = routes.test(body);
    else if (url.pathname === "/settings/llm/models")
      reply = routes.models
        ? routes.models(url)
        : { body: { models: [], source: "preset" } };
    const status = reply.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => reply.body,
    };
  });
}
const calls = (fetchMock: ReturnType<typeof router>, method: string, path: string) =>
  fetchMock.mock.calls.filter(
    ([url, init]) =>
      new URL(url).pathname === path && (init?.method ?? "GET") === method,
  );

beforeEach(() => {
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderModal = async (
  routes: Routes,
  props: Partial<Parameters<typeof SettingsModal>[0]> = {},
) => {
  const fetchMock = router(routes);
  vi.stubGlobal("fetch", fetchMock);
  const onSave = vi.fn(async () => undefined);
  const onClose = vi.fn();
  const onLlmSaved = vi.fn();
  render(
    <SettingsModal
      current={defaultSettings}
      busy={false}
      onSave={onSave}
      onClose={onClose}
      onLlmSaved={onLlmSaved}
      {...props}
    />,
  );
  await screen.findByRole("heading", { name: "Yapay zeka" });
  return { fetchMock, onSave, onClose, onLlmSaved };
};
const radio = (name: string) =>
  screen.getByRole("radio", { name }) as HTMLInputElement;
const keyField = () =>
  screen.getByLabelText("API anahtarı") as HTMLInputElement;

describe("Yapay zeka settings", () => {
  it("loads presets, the saved model and a calm saved-key chip", async () => {
    await renderModal({ llm: () => ({ body: llmFixture() }) });
    await screen.findByText("Anahtar kayıtlı");
    expect(radio("EVREN").checked).toBe(true);
    expect(radio("DeepSeek").checked).toBe(false);
    expect(radio("Diğer")).toBeTruthy();
    const gemma = screen.getByRole("radio", { name: /Gemma 4 31B/ }) as HTMLInputElement;
    expect(gemma.checked).toBe(true);
    expect(gemma.closest("label")?.textContent).toContain("Önerilen");
    expect(screen.getByText("…X45NMo")).toBeTruthy();
    expect(screen.queryByLabelText("API anahtarı")).toBeNull();
    expect(
      (screen.getByLabelText("Yedek model") as HTMLSelectElement).value,
    ).toBe("gemma-4-12b");
    expect(
      (screen.getByRole("switch", { name: /Hızlı mod/ }) as HTMLInputElement)
        .checked,
    ).toBe(true);
  });

  it("switching provider fills base_url and loads that provider's models", async () => {
    const { fetchMock } = await renderModal({
      llm: () => ({ body: llmFixture() }),
      models: (url) => ({
        body:
          url.searchParams.get("preset") === "deepseek"
            ? {
                models: [
                  { id: "deepseek-chat", label: "DeepSeek Chat", note: "Genel" },
                  { id: "deepseek-v4", label: "DeepSeek V4", note: "API’den" },
                ],
                source: "api",
              }
            : { models: [], source: "preset" },
      }),
    });
    await screen.findByText("Anahtar kayıtlı");
    fireEvent.click(radio("DeepSeek"));
    expect(screen.getByText("https://api.deepseek.com")).toBeTruthy();
    expect(
      (screen.getByRole("radio", { name: /DeepSeek Chat/ }) as HTMLInputElement)
        .checked,
    ).toBe(true);
    await screen.findByRole("radio", { name: /DeepSeek V4/ });
    const modelCall = calls(fetchMock, "GET", "/settings/llm/models").at(-1)!;
    const query = new URL(modelCall[0]).searchParams;
    expect(query.get("base_url")).toBe("https://api.deepseek.com");
    expect(query.get("preset")).toBe("deepseek");
    // A new provider asks for its own key, with its hint as placeholder.
    expect(keyField().placeholder).toBe("sk-…");
    fireEvent.click(radio("Diğer"));
    const base = screen.getByLabelText("Sağlayıcı adresi") as HTMLInputElement;
    expect(base.value).toBe("");
    expect(screen.getByLabelText("Model adı")).toBeTruthy();
  });

  it("pastes the key trimmed (button and Ctrl+V)", async () => {
    const readText = vi.fn(async () => "  sk-live-123 \n");
    vi.stubGlobal("navigator", { ...navigator, clipboard: { readText } });
    await renderModal({ llm: () => ({ body: llmFixture() }) });
    fireEvent.click(await screen.findByRole("button", { name: "Değiştir" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Yapıştır/ }));
    });
    expect(keyField().value).toBe("sk-live-123");
    fireEvent.paste(keyField(), {
      clipboardData: { getData: () => "\tsk-other-9  " },
    });
    expect(keyField().value).toBe("sk-other-9");
    expect(keyField().type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Anahtarı göster" }));
    expect(keyField().type).toBe("text");
  });

  it("explains a clipboard failure instead of throwing", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: {
        readText: vi.fn(async () => {
          throw new DOMException("denied", "NotAllowedError");
        }),
      },
    });
    await renderModal({
      llm: () => ({ body: { ...llmFixture(), api_key_set: false, api_key_hint: null } }),
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: /Yapıştır/ }));
    });
    expect(screen.getByText(/Panoya erişilemedi/)).toBeTruthy();
  });

  it("Save sends api_key only when a new one was entered", async () => {
    const put = vi.fn((body: Record<string, unknown>) => ({
      body: { ...llmFixture(), ...body, api_key: undefined },
    }));
    const { onLlmSaved, onSave, onClose } = await renderModal({
      llm: () => ({ body: llmFixture() }),
      put,
    });
    await screen.findByText("Anahtar kayıtlı");
    fireEvent.click(screen.getByRole("switch", { name: /Hızlı mod/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][0]).toEqual({ thinking: "on" });
    expect(onLlmSaved).toHaveBeenCalledWith(
      expect.objectContaining({ model: "gemma-4-31b" }),
    );
    expect(onSave).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    cleanup();

    const put2 = vi.fn((body: Record<string, unknown>) => ({
      body: { ...llmFixture(), thinking: body.thinking ?? "off" },
    }));
    await renderModal({ llm: () => ({ body: llmFixture() }), put: put2 });
    fireEvent.click(await screen.findByRole("button", { name: "Değiştir" }));
    fireEvent.change(keyField(), { target: { value: " sk-new-key " } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(put2.mock.calls[0][0]).toEqual({ api_key: "sk-new-key" });
    // The key never touches browser storage.
    expect(JSON.stringify({ ...localStorage })).not.toContain("sk-new-key");
  });

  it("does not PUT when nothing changed, and shows 422 detail inline", async () => {
    const put = vi.fn(() => ({
      status: 422,
      body: { detail: "Model adı bu sağlayıcıda bulunamadı." },
    }));
    const { onClose, onLlmSaved } = await renderModal({
      llm: () => ({ body: llmFixture() }),
      put,
    });
    await screen.findByText("Anahtar kayıtlı");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(put).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
    onClose.mockClear();
    fireEvent.click(screen.getByRole("radio", { name: /Gemma 4 12B/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(put).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert").textContent).toBe(
      "Model adı bu sağlayıcıda bulunamadı.",
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(onLlmSaved).not.toHaveBeenCalled();
  });

  it("tests the connection: spinner, success line, and Turkish error", async () => {
    let resolveTest: (reply: Reply) => void = () => undefined;
    const test = vi.fn(
      (_body: Record<string, unknown>) =>
        new Promise<Reply>((resolve) => {
          resolveTest = resolve;
        }),
    );
    const fetchMock = router({ llm: () => ({ body: llmFixture() }) });
    const wrapped = vi.fn(async (input: string, init?: RequestInit) => {
      if (new URL(input).pathname === "/settings/llm/test") {
        const reply = await test(JSON.parse(init!.body as string));
        return {
          ok: (reply.status ?? 200) < 300,
          status: reply.status ?? 200,
          json: async () => reply.body,
        };
      }
      return fetchMock(input, init);
    });
    vi.stubGlobal("fetch", wrapped);
    render(
      <SettingsModal
        current={defaultSettings}
        busy={false}
        onSave={async () => undefined}
        onClose={() => undefined}
      />,
    );
    await screen.findByText("Anahtar kayıtlı");
    const button = screen.getByRole("button", { name: /Bağlantıyı test et/ });
    await act(async () => {
      fireEvent.click(button);
    });
    const busy = screen.getByRole("button", { name: /Deneniyor/ }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(test.mock.calls[0][0]).toEqual({
      base_url: "https://evren.example/v1",
      model: "gemma-4-31b",
    });
    await act(async () => {
      resolveTest({
        body: { ok: true, latency_ms: 812, model: "gemma-4-31b", message: "Bağlantı başarılı" },
      });
    });
    expect(await screen.findByText("Bağlantı başarılı · 812 ms")).toBeTruthy();
    expect(
      screen.getByText("Bağlantı başarılı · 812 ms").closest("[aria-live]"),
    ).toBeTruthy();

    // Enter in the key field runs the test with the new key.
    fireEvent.click(screen.getByRole("button", { name: "Değiştir" }));
    fireEvent.change(keyField(), { target: { value: "sk-bad" } });
    await act(async () => {
      fireEvent.keyDown(keyField(), { key: "Enter" });
    });
    expect(test.mock.calls[1][0]).toMatchObject({ api_key: "sk-bad" });
    await act(async () => {
      resolveTest({
        body: { ok: false, latency_ms: null, model: null, message: "Anahtar reddedildi (401)." },
      });
    });
    const failure = await screen.findByText("Anahtar reddedildi (401).");
    expect(failure.closest(".test-result")?.className).toContain("fail");
  });

  it("falls back to a small 'Sunucuyu güncelle' note on 404 and keeps the rest usable", async () => {
    const { onSave } = await renderModal({});
    expect(await screen.findByText("Sunucuyu güncelle")).toBeTruthy();
    const backend = screen.getByLabelText("Sunucu adresi") as HTMLInputElement;
    fireEvent.change(backend, { target: { value: "http://localhost:8766" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ backend: "http://localhost:8766" }),
    );
  });

  it("formats model ids for people", () => {
    expect(prettyModel("gemma-4-31b")).toBe("Gemma 4 31B");
    expect(prettyModel("google/gemma-4-31b-it")).toBe("Gemma 4 31B");
    expect(prettyModel("deepseek-chat")).toBe("DeepSeek Chat");
    expect(
      prettyModel("x", [{ id: "x", label: "Etiketli", note: "" }]),
    ).toBe("Etiketli");
  });
});

class StubSocket {
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(_url: string) {}
  send() {}
  close() {
    this.readyState = 3;
  }
}

describe("First run: missing key", () => {
  it("'Anahtarı gir' opens settings at the key field, saves and confirms", async () => {
    let configured = false;
    const put = vi.fn((body: Record<string, unknown>) => {
      configured = true;
      return {
        body: {
          ...llmFixture(),
          api_key_set: true,
          api_key_hint: `…${String(body.api_key).slice(-6)}`,
          llm_configured: true,
        },
      };
    });
    const fetchMock = router({
      health: () => ({
        body: {
          ok: true,
          service: "polaris",
          llm_model: "gemma-4-31b",
          llm_configured: configured,
        },
      }),
      llm: () => ({
        body: {
          ...llmFixture(),
          api_key_set: false,
          api_key_hint: null,
          llm_configured: false,
        },
      }),
      put,
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", StubSocket);
    render(<App />);
    expect(
      await screen.findByText("Yapay zekâ anahtarı ayarlı değil"),
    ).toBeTruthy();
    expect(screen.getByText("Anahtar yok")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Anahtarı gir/ }));
    await waitFor(() => expect(document.activeElement).toBe(keyField()));
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    fireEvent.change(keyField(), { target: { value: "evr-abcdefX45NMo" } });
    const healthBefore = calls(fetchMock, "GET", "/health").length;
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    });
    expect(put.mock.calls[0][0]).toEqual({ api_key: "evr-abcdefX45NMo" });
    expect(await screen.findByText("Kaydedildi · model gemma-4-31b")).toBeTruthy();
    await waitFor(() =>
      expect(calls(fetchMock, "GET", "/health").length).toBeGreaterThan(
        healthBefore,
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("Yapay zekâ anahtarı ayarlı değil")).toBeNull(),
    );
    // Header shows the model in human form.
    expect(screen.getByTitle("Etkin model").textContent).toBe("Gemma 4 31B");
  });
});
