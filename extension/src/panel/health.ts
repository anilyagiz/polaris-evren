import { normalizeBackend } from "./protocol";

/** `/health` payload. Only `ok` is guaranteed; everything else is optional. */
export interface Health {
  ok: boolean;
  llm_model: string | null;
  llm_configured: boolean | null;
  cua_enabled: boolean | null;
  stt_ready: boolean | null;
  brave_configured: boolean | null;
  version: string | null;
  service: string | null;
}
export type HealthState =
  | { kind: "checking" }
  | { kind: "unreachable"; message: string }
  | { kind: "ok"; health: Health; checkedAt: number };

/** One-click launcher in the repo root (also used by the tray app). */
export const RUN_BACKEND_COMMAND = "start-polaris.cmd";
/** Developer alternative with live reload. */
export const RUN_BACKEND_SCRIPT = ".\\scripts\\run-backend.ps1";

const bool = (value: unknown) => (typeof value === "boolean" ? value : null);
const text = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : null;

export function parseHealth(value: unknown): Health | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  if (raw.ok !== true) return null;
  return {
    ok: true,
    llm_model: text(raw.llm_model),
    llm_configured: bool(raw.llm_configured),
    cua_enabled: bool(raw.cua_enabled),
    stt_ready: bool(raw.stt_ready),
    brave_configured: bool(raw.brave_configured),
    version: text(raw.version),
    service: text(raw.service),
  };
}

export async function checkHealth(
  backend: string,
  timeoutMs = 4000,
): Promise<HealthState> {
  let url: string;
  try {
    url = `${normalizeBackend(backend)}/health`;
  } catch {
    return { kind: "unreachable", message: "Backend adresi geçersiz." };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok)
      return {
        kind: "unreachable",
        message: `Sunucu hata döndürdü (HTTP ${response.status}).`,
      };
    const health = parseHealth(await response.json());
    if (!health)
      return {
        kind: "unreachable",
        message: "Bu adresteki sunucu Polaris gibi görünmüyor.",
      };
    return { kind: "ok", health, checkedAt: Date.now() };
  } catch (cause) {
    return {
      kind: "unreachable",
      message:
        cause instanceof DOMException && cause.name === "AbortError"
          ? "Sunucu zamanında yanıt vermedi."
          : "Sunucuya ulaşılamadı.",
    };
  } finally {
    clearTimeout(timer);
  }
}


/**
 * Ports the Polaris tray tries, in order, when its configured port is taken
 * (tray/settings.py FALLBACK_PORTS). The panel probes the same list.
 */
export const FALLBACK_PORTS = [8765, 8766, 8767, 47615, 47616];

const isLocal = (backend: string) =>
  /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/i.test(backend.trim());

/**
 * When the configured local backend is not Polaris, look for one on the
 * fallback ports. Returns the base URL of a backend that identifies itself as
 * Polaris, or null. Remote (non-localhost) addresses are never rewritten.
 */
export async function discoverBackend(
  current: string,
  probe: typeof checkHealth = checkHealth,
): Promise<string | null> {
  if (!isLocal(current)) return null;
  const host = /127\.0\.0\.1/.test(current) ? "127.0.0.1" : "localhost";
  const normalizedCurrent = current.trim().replace(/\/+$/, "");
  for (const port of FALLBACK_PORTS) {
    const candidate = `http://${host}:${port}`;
    if (candidate === normalizedCurrent) continue;
    const result = await probe(candidate, 1200);
    if (result.kind === "ok" && result.health.service === "polaris") return candidate;
  }
  return null;
}
