import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Container,
  ExternalLink,
  Layers,
  LoaderCircle,
  Monitor,
  Package,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import {
  getDesktopStatus,
  setDesktopEnabled,
  startDesktopInstall,
  type DesktopStatus,
} from "./desktop";
import { LlmApiError } from "./llm";

export const DESKTOP_POLL_MS = 2000;

type Load =
  | { kind: "loading" }
  | { kind: "ready"; status: DesktopStatus }
  | { kind: "hidden" };

/**
 * "Masaüstü kontrolü" card: one click installs the CUA packages + Docker image
 * and turns the feature on live. Hidden when the server does not support it.
 */
export function DesktopSection({ backend }: { backend: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [action, setAction] = useState<"" | "install" | "toggle" | "check">("");
  const [error, setError] = useState("");
  const alive = useRef(true);

  const refresh = useCallback(
    async (force = false) => {
      try {
        const status = await getDesktopStatus(backend, force);
        if (alive.current) setLoad({ kind: "ready", status });
        return status;
      } catch (cause) {
        if (!alive.current) return null;
        // Older server (404) or unreachable: the Yapay zeka card already explains.
        setLoad((prev) => (prev.kind === "ready" ? prev : { kind: "hidden" }));
        if (cause instanceof LlmApiError && cause.status !== 404 && cause.status !== 0)
          setError(cause.message);
        return null;
      }
    },
    [backend],
  );

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  const installing = load.kind === "ready" && load.status.installing;
  useEffect(() => {
    if (!installing) return;
    const timer = setInterval(() => void refresh(), DESKTOP_POLL_MS);
    return () => clearInterval(timer);
  }, [installing, refresh]);

  if (load.kind === "hidden") return null;

  const run = async (
    kind: "install" | "toggle" | "check",
    fn: () => Promise<DesktopStatus | null>,
  ) => {
    setAction(kind);
    setError("");
    try {
      const status = await fn();
      if (status && alive.current) setLoad({ kind: "ready", status });
    } catch (cause) {
      if (alive.current)
        setError(cause instanceof Error ? cause.message : "İşlem başarısız oldu.");
    } finally {
      if (alive.current) setAction("");
    }
  };

  if (load.kind === "loading")
    return (
      <section className="llm-section desktop-section" aria-busy="true" aria-label="Masaüstü kontrolü">
        <DesktopHead />
        <div className="llm-skeleton" aria-hidden="true">
          <i />
          <i />
        </div>
      </section>
    );

  const s = load.status;
  const progress = s.install_progress;
  const needsInstall = !s.packages_installed || !s.image_present;
  const showProgress = s.installing && progress;

  return (
    <section className="llm-section desktop-section" aria-labelledby="desktop-title">
      <DesktopHead status={s} />
      <p className="desktop-lede">
        Polaris, izole bir Linux masaüstünde uygulama açıp tıklayabilir. Gerçek
        masaüstüne dokunmaz.
      </p>

      <ul className="desktop-checks" aria-label="Gereksinimler">
        <Check
          icon={<Package size={14} />}
          label="Paketler"
          ok={s.packages_installed}
          busy={s.installing && progress?.step === "packages"}
          detail={s.packages_installed ? "Kurulu" : "Kurulmadı"}
        />
        <Check
          icon={<Container size={14} />}
          label="Docker"
          ok={s.docker_running}
          warn={s.docker_installed && !s.docker_running}
          busy={s.installing && progress?.step === "docker"}
          detail={
            s.docker_running
              ? "Çalışıyor"
              : s.docker_installed
                ? "Kapalı"
                : "Kurulu değil"
          }
        />
        <Check
          icon={<Layers size={14} />}
          label="Masaüstü imajı"
          ok={s.image_present}
          busy={s.installing && progress?.step === "image"}
          detail={s.image_present ? "İndirildi" : "~1,3 GB"}
        />
      </ul>

      {showProgress && (
        <div className="desktop-progress" role="status" aria-live="polite">
          <div
            className={`progress-track ${progress.percent === null ? "indeterminate" : ""}`}
            role="progressbar"
            aria-label="Kurulum ilerlemesi"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress.percent ?? undefined}
          >
            <span style={progress.percent === null ? undefined : { width: `${progress.percent}%` }} />
          </div>
          <p className="field-help">{progress.message}</p>
        </div>
      )}

      {!s.installing && !s.docker_installed && (
        <div className="desktop-actions">
          <a
            className="primary full link-button"
            href={s.docker_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink size={14} /> Docker Desktop’ı indir
          </a>
          <p className="field-help">
            Docker Desktop’ı kurup açtıktan sonra “Tekrar kontrol et”e bas.
            Kurulum bir kez yeniden başlatma isteyebilir.
          </p>
        </div>
      )}
      {!s.installing && s.docker_installed && !s.docker_running && (
        <p className="field-help warn">
          Docker Desktop çalışmıyor. Açıp sağ altta “Engine running” yazana
          kadar bekle, sonra tekrar kontrol et.
        </p>
      )}
      {!s.installing && s.docker_running && needsInstall && (
        <div className="desktop-actions">
          <button
            type="button"
            className="primary full"
            disabled={action !== ""}
            onClick={() => void run("install", () => startDesktopInstall(backend))}
          >
            {action === "install" ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <Monitor size={14} />
            )}
            Kur ve aç
          </button>
          <p className="field-help">
            Python paketleri ve {s.image_present ? "" : "~1,3 GB’lık "}Linux
            masaüstü imajı indirilir; bir kez yapılır.
          </p>
        </div>
      )}

      {!needsInstall && s.docker_installed && (
        <label className="setting-row desktop-toggle">
          <span>
            <strong>{s.enabled ? "Açık" : "Kapalı"}</strong>
            <small>
              {s.enabled
                ? "Masaüstü görevleri izole Linux masaüstünde yapılır."
                : "Açınca “masaüstünde terminal aç” gibi istekleri yapabilirim."}
            </small>
          </span>
          <input
            type="checkbox"
            role="switch"
            className="switch"
            aria-label="Masaüstü kontrolü"
            checked={s.enabled}
            disabled={action !== "" || s.installing || (!s.enabled && !s.ready)}
            onChange={(event) => {
              const next = event.target.checked;
              void run("toggle", () => setDesktopEnabled(backend, next));
            }}
          />
        </label>
      )}

      {(s.last_error || error) && !s.installing && (
        <p className="test-result fail" role="alert">
          <TriangleAlert size={13} />
          <span>
            {error || s.last_error}
            {(error || s.last_error || "").includes("Docker Desktop") &&
              !s.docker_installed && (
                <>
                  {" "}
                  <a href={s.docker_url} target="_blank" rel="noopener noreferrer">
                    İndir
                  </a>
                </>
              )}
          </span>
        </p>
      )}

      {!s.installing && !(s.ready && s.enabled) && (
        <button
          type="button"
          className="text-button desktop-recheck"
          disabled={action !== ""}
          onClick={() => void run("check", () => refresh(true))}
        >
          <RefreshCw size={13} className={action === "check" ? "spin" : ""} />{" "}
          Tekrar kontrol et
        </button>
      )}
    </section>
  );
}

function Check({
  icon,
  label,
  ok,
  warn = false,
  busy = false,
  detail,
}: {
  icon: ReactNode;
  label: string;
  ok: boolean;
  warn?: boolean;
  busy?: boolean;
  detail: string;
}) {
  const state = busy ? "busy" : ok ? "ok" : warn ? "warn" : "todo";
  return (
    <li className={`desktop-check ${state}`} data-state={state}>
      <span className="desktop-check-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="desktop-check-label">{label}</span>
      <span className="desktop-check-state">
        {busy ? (
          <LoaderCircle size={13} className="spin" aria-hidden="true" />
        ) : ok ? (
          <CircleCheck size={13} aria-hidden="true" />
        ) : warn ? (
          <CircleAlert size={13} aria-hidden="true" />
        ) : (
          <CircleDashed size={13} aria-hidden="true" />
        )}
        {busy ? "Sürüyor" : detail}
      </span>
    </li>
  );
}

function DesktopHead({ status }: { status?: DesktopStatus }) {
  let chip: { cls: string; text: string } | null = null;
  if (status) {
    if (status.installing) chip = { cls: "warn", text: "Kuruluyor" };
    else if (status.enabled && status.ready) chip = { cls: "ok", text: "Açık" };
    else if (status.enabled) chip = { cls: "warn", text: "Açık · Docker gerekli" };
    else if (status.ready) chip = { cls: "idle", text: "Kapalı" };
    else chip = { cls: "warn", text: "Kurulum gerekli" };
  }
  return (
    <div className="section-head">
      <div>
        <span className="eyebrow">Eller</span>
        <h3 id="desktop-title">Masaüstü kontrolü</h3>
      </div>
      {chip && (
        <span className={`status-chip ${chip.cls}`}>
          <i aria-hidden="true" />
          {chip.text}
        </span>
      )}
    </div>
  );
}
