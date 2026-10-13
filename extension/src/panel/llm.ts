import { normalizeBackend } from "./protocol";

/** Client for the backend's live LLM settings (`/settings/llm`). */

export type ProviderId = "evren" | "deepseek" | "custom";
export interface LlmModel {
  id: string;
  label: string;
  note: string;
}
export interface LlmPreset {
  id: ProviderId;
  label: string;
  base_url: string;
  key_hint: string;
  models: LlmModel[];
  recommended: {
    model: string;
    fallback_model: string | null;
    thinking: "on" | "off";
  } | null;
}
export interface LlmSettings {
  presets: LlmPreset[];
  provider: ProviderId;
  base_url: string;
  model: string;
  fallback_model: string | null;
  thinking: "on" | "off";
  api_key_set: boolean;
  api_key_hint: string | null;
  brave_key_set: boolean;
  llm_configured: boolean;
}
export interface LlmUpdate {
  base_url?: string;
  model?: string;
  fallback_model?: string | null;
  thinking?: "on" | "off";
  api_key?: string;
  brave_api_key?: string;
}
export interface LlmTestResult {
  ok: boolean;
  latency_ms: number | null;
  model: string | null;
  message: string;
}

/** A request failed; `status` 0 means the server could not be reached. */
export class LlmApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

const str = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : fallback;
const providerId = (value: unknown): ProviderId =>
  value === "evren" || value === "deepseek" ? value : "custom";
const thinkingOf = (value: unknown): "on" | "off" =>
  value === "off" ? "off" : "on";

function parseModels(value: unknown): LlmModel[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (item): item is Record<string, unknown> =>
        typeof item === "object" && item !== null && typeof item.id === "string",
    )
    .map((item) => ({
      id: item.id as string,
      label: str(item.label) || prettyModel(item.id as string),
      note: str(item.note),
    }));
}

export function parseLlmSettings(value: unknown): LlmSettings | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  if (!Array.isArray(raw.presets)) return null;
  const presets: LlmPreset[] = raw.presets
    .filter(
      (item): item is Record<string, unknown> =>
        typeof item === "object" && item !== null,
    )
    .map((item) => {
      const rec =
        typeof item.recommended === "object" && item.recommended !== null
          ? (item.recommended as Record<string, unknown>)
          : null;
      return {
        id: providerId(item.id),
        label: str(item.label, str(item.id)),
        base_url: str(item.base_url),
        key_hint: str(item.key_hint),
        models: parseModels(item.models),
        recommended:
          rec && typeof rec.model === "string"
            ? {
                model: rec.model,
                fallback_model:
                  typeof rec.fallback_model === "string"
                    ? rec.fallback_model
                    : null,
                thinking: thinkingOf(rec.thinking),
              }
            : null,
      };
    });
  return {
    presets,
    provider: providerId(raw.provider),
    base_url: str(raw.base_url),
    model: str(raw.model),
    fallback_model:
      typeof raw.fallback_model === "string" && raw.fallback_model
        ? raw.fallback_model
        : null,
    thinking: thinkingOf(raw.thinking),
    api_key_set: raw.api_key_set === true,
    api_key_hint:
      typeof raw.api_key_hint === "string" && raw.api_key_hint
        ? raw.api_key_hint
        : null,
    brave_key_set: raw.brave_key_set === true,
    llm_configured: raw.llm_configured === true,
  };
}

async function request(
  backend: string,
  path: string,
  init: RequestInit = {},
  timeoutMs = 8000,
): Promise<unknown> {
  let url: string;
  try {
    url = `${normalizeBackend(backend)}${path}`;
  } catch {
    throw new LlmApiError("Sunucu adresi geçersiz.", 0);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      signal: controller.signal,
      headers: init.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch (cause) {
    throw new LlmApiError(
      cause instanceof DOMException && cause.name === "AbortError"
        ? "Sunucu zamanında yanıt vermedi."
        : "Sunucuya ulaşılamadı.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).detail
        : null;
    throw new LlmApiError(
      typeof detail === "string" && detail
        ? detail
        : `Sunucu hata döndürdü (HTTP ${response.status}).`,
      response.status,
    );
  }
  return body;
}

const settingsOrThrow = (body: unknown) => {
  const parsed = parseLlmSettings(body);
  if (!parsed)
    throw new LlmApiError("Sunucudan beklenmeyen bir yanıt geldi.", 500);
  return parsed;
};

export async function getLlmSettings(backend: string): Promise<LlmSettings> {
  return settingsOrThrow(await request(backend, "/settings/llm"));
}
export async function putLlmSettings(
  backend: string,
  update: LlmUpdate,
): Promise<LlmSettings> {
  return settingsOrThrow(
    await request(backend, "/settings/llm", {
      method: "PUT",
      body: JSON.stringify(update),
    }),
  );
}
export async function testLlm(
  backend: string,
  payload: { base_url?: string; model?: string; api_key?: string },
): Promise<LlmTestResult> {
  const body = await request(
    backend,
    "/settings/llm/test",
    { method: "POST", body: JSON.stringify(payload) },
    30000,
  );
  const raw = (typeof body === "object" && body !== null ? body : {}) as Record<
    string,
    unknown
  >;
  return {
    ok: raw.ok === true,
    latency_ms: typeof raw.latency_ms === "number" ? raw.latency_ms : null,
    model: typeof raw.model === "string" ? raw.model : null,
    message: str(raw.message) || (raw.ok === true ? "Bağlantı başarılı." : "Bağlantı kurulamadı."),
  };
}
export async function listModels(
  backend: string,
  baseUrl: string,
  preset: ProviderId,
): Promise<LlmModel[]> {
  const query = new URLSearchParams({ base_url: baseUrl, preset });
  const body = await request(backend, `/settings/llm/models?${query}`);
  const raw = (typeof body === "object" && body !== null ? body : {}) as Record<
    string,
    unknown
  >;
  return parseModels(raw.models);
}

const brands: Record<string, string> = {
  deepseek: "DeepSeek",
  gemma: "Gemma",
  gemini: "Gemini",
  gpt: "GPT",
  llama: "Llama",
  qwen: "Qwen",
  mistral: "Mistral",
  mixtral: "Mixtral",
  claude: "Claude",
  phi: "Phi",
  chat: "Chat",
  reasoner: "Reasoner",
  coder: "Coder",
};
const dropped = new Set(["it", "instruct", "latest"]);

/** Human name for a model id: "gemma-4-31b" → "Gemma 4 31B". */
export function prettyModel(id: string, models: LlmModel[] = []): string {
  const known = models.find((model) => model.id === id);
  if (known?.label) return known.label;
  const base = id.trim().split("/").pop() ?? id;
  const words = base
    .split(/[-_:\s]+/)
    .filter((word) => word && !dropped.has(word.toLowerCase()));
  if (!words.length) return id;
  return words
    .map((word) => {
      const lower = word.toLowerCase();
      if (brands[lower]) return brands[lower];
      if (/^\d+(\.\d+)?[bmk]$/i.test(word)) return word.toUpperCase();
      if (/^v?\d/.test(word)) return word;
      return word.charAt(0).toUpperCase() + word.slice(1);
    })
    .join(" ");
}
