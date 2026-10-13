import { LlmApiError, request } from "./llm";

/** Client for the backend's "Masaüstü kontrolü" settings (`/settings/desktop`). */

export const DOCKER_URL = "https://www.docker.com/products/docker-desktop/";

export interface DesktopProgress {
  step: string;
  message: string;
  percent: number | null;
}
export interface DesktopStatus {
  enabled: boolean;
  packages_installed: boolean;
  docker_installed: boolean;
  docker_running: boolean;
  image_present: boolean;
  image_name: string;
  docker_url: string;
  installing: boolean;
  install_progress: DesktopProgress | null;
  last_error: string | null;
  ready: boolean;
}

export function parseDesktopStatus(value: unknown): DesktopStatus | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.enabled !== "boolean") return null;
  const flag = (key: string) => raw[key] === true;
  const prog =
    typeof raw.install_progress === "object" && raw.install_progress !== null
      ? (raw.install_progress as Record<string, unknown>)
      : null;
  return {
    enabled: raw.enabled,
    packages_installed: flag("packages_installed"),
    docker_installed: flag("docker_installed"),
    docker_running: flag("docker_running"),
    image_present: flag("image_present"),
    image_name: typeof raw.image_name === "string" ? raw.image_name : "",
    docker_url:
      typeof raw.docker_url === "string" && /^https:\/\//.test(raw.docker_url)
        ? raw.docker_url
        : DOCKER_URL,
    installing: flag("installing"),
    install_progress: prog
      ? {
          step: typeof prog.step === "string" ? prog.step : "",
          message: typeof prog.message === "string" ? prog.message : "",
          percent:
            typeof prog.percent === "number"
              ? Math.max(0, Math.min(100, prog.percent))
              : null,
        }
      : null,
    last_error:
      typeof raw.last_error === "string" && raw.last_error
        ? raw.last_error
        : null,
    ready: flag("ready"),
  };
}

const statusOrThrow = (body: unknown) => {
  const parsed = parseDesktopStatus(body);
  if (!parsed)
    throw new LlmApiError("Sunucudan beklenmeyen bir yanıt geldi.", 500);
  return parsed;
};

export async function getDesktopStatus(
  backend: string,
  refresh = false,
): Promise<DesktopStatus> {
  return statusOrThrow(
    await request(
      backend,
      refresh ? "/settings/desktop?refresh=true" : "/settings/desktop",
      {},
      20000,
    ),
  );
}
export async function startDesktopInstall(
  backend: string,
): Promise<DesktopStatus> {
  return statusOrThrow(
    await request(
      backend,
      "/settings/desktop/install",
      { method: "POST", body: JSON.stringify({ enable: true }) },
      20000,
    ),
  );
}
export async function setDesktopEnabled(
  backend: string,
  enabled: boolean,
): Promise<DesktopStatus> {
  return statusOrThrow(
    await request(
      backend,
      "/settings/desktop",
      { method: "PUT", body: JSON.stringify({ enabled }) },
      20000,
    ),
  );
}
