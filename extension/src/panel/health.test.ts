import { afterEach, describe, expect, it, vi } from "vitest";
import { friendlyError, isToolFailure } from "./errors";
import { checkHealth, discoverBackend, parseHealth } from "./health";

afterEach(() => vi.unstubAllGlobals());

it("accepts the minimal and extended /health payloads", () => {
  expect(parseHealth({ ok: true })).toMatchObject({
    ok: true,
    llm_model: null,
    llm_configured: null,
  });
  expect(
    parseHealth({
      ok: true,
      llm_model: "deepseek-chat",
      llm_configured: true,
      cua_enabled: false,
      stt_ready: "yes",
      brave_configured: true,
      version: "0.2.0",
      extra: [1],
    }),
  ).toEqual({
    ok: true,
    llm_model: "deepseek-chat",
    llm_configured: true,
    cua_enabled: false,
    stt_ready: null,
    brave_configured: true,
    version: "0.2.0",
    service: null,
  });
  expect(parseHealth({ ok: false })).toBeNull();
  expect(parseHealth("ok")).toBeNull();
});
it("reports unreachable servers, HTTP errors and foreign servers", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("x")));
  expect((await checkHealth("http://localhost:8000")).kind).toBe(
    "unreachable",
  );
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: false, status: 502 }),
  );
  expect(await checkHealth("http://localhost:8000")).toEqual({
    kind: "unreachable",
    message: "Sunucu hata döndürdü (HTTP 502).",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({ hello: 1 }) }),
  );
  expect((await checkHealth("http://localhost:8000")).kind).toBe(
    "unreachable",
  );
  expect((await checkHealth("nope")).kind).toBe("unreachable");
});
it("returns parsed health on success", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true, cua_enabled: true }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const result = await checkHealth("http://127.0.0.1:8000/");
  expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/health");
  expect(result.kind === "ok" && result.health.cua_enabled).toBe(true);
});
it("maps backend LLM errors to friendly Turkish titles", () => {
  const title = (message: string) => friendlyError(message).title;
  expect(
    title("LLM istek sınırına takıldı (429); biraz bekleyip tekrar dene."),
  ).toBe("Yapay zekâ servisi şu an yoğun");
  expect(title("LLM zaman aşımına uğradı; biraz sonra tekrar dene.")).toBe(
    "Yanıt zamanında gelmedi",
  );
  expect(
    title("LLM_MODEL tanımlı değil; backend/.env dosyasında modeli belirt."),
  ).toBe("Model ayarı hatalı");
  expect(title("LLM sunucusu geçici hata verdi (503)")).toBe(
    "Yapay zekâ servisi geçici olarak yanıt vermiyor",
  );
  const unknown = friendlyError("tuhaf bir şey");
  expect(unknown.title).toBe("Bir sorun oluştu");
  expect(unknown.detail).toBe("tuhaf bir şey");
  expect(
    isToolFailure("Araç hatası: araç zaman aşımı: browser.read (20 sn)"),
  ).toBe(true);
  expect(isToolFailure("Sayfayı okudum")).toBe(false);
});

describe("discoverBackend", () => {
  const ok = (service: string | null) =>
    ({
      kind: "ok",
      checkedAt: 0,
      health: {
        ok: true, llm_model: null, llm_configured: true, cua_enabled: null,
        stt_ready: null, brave_configured: null, version: "0.1.0", service,
      },
    }) as const;

  it("finds Polaris on a fallback port when the configured one is taken", async () => {
    const probe = vi.fn(async (url: string) =>
      url === "http://localhost:8766" ? ok("polaris") : { kind: "unreachable" as const, message: "x" },
    );
    expect(await discoverBackend("http://localhost:8765", probe)).toBe("http://localhost:8766");
    expect(probe).not.toHaveBeenCalledWith("http://localhost:8765", expect.anything());
  });

  it("ignores other services and remote addresses", async () => {
    const probe = vi.fn(async () => ok(null));
    expect(await discoverBackend("http://localhost:8765", probe)).toBeNull();
    expect(await discoverBackend("https://polaris.example.com", probe)).toBeNull();
  });
});
