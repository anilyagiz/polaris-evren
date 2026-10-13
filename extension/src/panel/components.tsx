import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  ServerOff,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { isToolFailure, type FriendlyError } from "./errors";
import {
  checkHealth,
  RUN_BACKEND_COMMAND,
  RUN_BACKEND_SCRIPT,
  type HealthState,
} from "./health";
import {
  normalizeBackend,
  safeUrl,
  shotUrl,
  type Citation,
  type Confirmation,
  type ConnectionStatus,
  type TraceStep,
} from "./protocol";
import {
  backendError,
  extensionVersion,
  isLocalBackend,
  type Settings,
} from "./settings";
import { prettyModel, type LlmSettings } from "./llm";
import { LlmSection, useLlmDraft } from "./LlmSettings";
import { DesktopSection } from "./DesktopSettings";

/** Four-pointed north star used as the brand mark. */
export function PolarisMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M12 1.5c.5 4.9 1.9 7.9 4.3 9.2.9.5 2.3.9 4.2 1.3-1.9.4-3.3.8-4.2 1.3-2.4 1.3-3.8 4.3-4.3 9.2-.5-4.9-1.9-7.9-4.3-9.2-.9-.5-2.3-.9-4.2-1.3 1.9-.4 3.3-.8 4.2-1.3 2.4-1.3 3.8-4.3 4.3-9.2Z"
        fill="currentColor"
      />
      <circle cx="19" cy="5" r="1.1" fill="currentColor" opacity=".55" />
    </svg>
  );
}

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

/** Copy button with a short "Kopyalandı" confirmation. */
export function CopyButton({
  value,
  label,
  className = "text-button",
  children,
}: {
  value: string;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = setTimeout(() => setState("idle"), 1600);
    return () => clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      title={label}
      onClick={() =>
        void copyText(value).then((ok) => setState(ok ? "done" : "failed"))
      }
    >
      {state === "done" ? <Check size={13} /> : <Copy size={13} />}
      <span aria-live="polite">
        {state === "done"
          ? "Kopyalandı"
          : state === "failed"
            ? "Kopyalanamadı"
            : (children ?? "Kopyala")}
      </span>
    </button>
  );
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ href, children }) =>
            safeUrl(href) ? (
              <a href={safeUrl(href)} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => (
            <span>{alt ? `[Görsel: ${alt}]` : "[Görsel]"}</span>
          ),
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  className = "",
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.showModal();
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${className}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="modal-heading">
        <h2>{title}</h2>
        <button className="icon-button" aria-label="Kapat" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function ConfirmModal({
  pending,
  deadline,
  onDecide,
}: {
  pending: Confirmation;
  deadline: number;
  onDecide: (approved: boolean) => void;
}) {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, Math.ceil((deadline - Date.now()) / 1000)),
  );
  const decideRef = useRef(onDecide);
  decideRef.current = onDecide;
  useEffect(() => {
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds === 0) decideRef.current(false);
    };
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [deadline]);
  const args = Object.entries(pending.args);
  return (
    <Modal
      title="Onayın gerekiyor"
      className="confirm-modal"
      onClose={() => onDecide(false)}
    >
      <div className="approval-head">
        <span className="approval-symbol">
          <ShieldCheck size={24} />
        </span>
        <p className="approval-summary">{pending.summary}</p>
      </div>
      <dl className="approval-details">
        <div>
          <dt>İşlem</dt>
          <dd>
            <code>{pending.tool}</code>
          </dd>
        </div>
        {args.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>
              <code>
                {typeof value === "string" ? value : JSON.stringify(value)}
              </code>
            </dd>
          </div>
        ))}
      </dl>
      <div className="countdown" role="timer">
        <div className="countdown-track" aria-hidden="true">
          <i style={{ width: `${Math.min(100, (remaining / 60) * 100)}%` }} />
        </div>
        <span>
          {remaining} saniye içinde cevap verilmezse işlem reddedilir.
        </span>
      </div>
      <div className="button-row">
        <button className="secondary" onClick={() => onDecide(false)}>
          Reddet
        </button>
        <button className="primary" onClick={() => onDecide(true)}>
          <Check size={16} /> Onayla
        </button>
      </div>
      <p className="approval-foot">
        Polaris onayın olmadan gönderme, satın alma veya silme yapmaz.
      </p>
    </Modal>
  );
}

type TestState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "ok"; text: string }
  | { kind: "warn"; text: string }
  | { kind: "fail"; text: string };

export function SettingsModal({
  current,
  busy,
  onSave,
  onClose,
  focusKey = false,
  onLlmSaved,
}: {
  current: Settings;
  busy: boolean;
  onSave: (settings: Settings) => Promise<void>;
  onClose: () => void;
  /** Open with the API key field focused (first-run "Anahtarı gir"). */
  focusKey?: boolean;
  onLlmSaved?: (settings: LlmSettings) => void;
}) {
  const [draft, setDraft] = useState(current);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestState>({ kind: "idle" });
  const llm = useLlmDraft(current.backend);
  const llmUnavailable =
    llm.load.kind === "missing" || llm.load.kind === "error";
  const [advanced, setAdvanced] = useState(false);
  const urlError = backendError(draft.backend);
  const remote = !urlError && !isLocalBackend(draft.backend);
  const showAdvanced = advanced || llmUnavailable || Boolean(urlError);
  const runTest = async () => {
    if (urlError) return;
    setTest({ kind: "testing" });
    const result = await checkHealth(draft.backend);
    if (result.kind === "unreachable")
      setTest({ kind: "fail", text: `Bağlanılamadı: ${result.message}` });
    else if (result.kind === "ok" && result.health.llm_configured === false)
      setTest({
        kind: "warn",
        text: "Sunucu çalışıyor ama yapay zekâ anahtarı ayarlı değil.",
      });
    else if (result.kind === "ok")
      setTest({
        kind: "ok",
        text: `Bağlantı başarılı${result.health.llm_model ? ` · model: ${result.health.llm_model}` : ""}`,
      });
  };
  return (
    <Modal title="Ayarlar" onClose={onClose} className="settings-modal">
      <form
        noValidate
        onSubmit={async (event) => {
          event.preventDefault();
          if (urlError || saving) return;
          setSaving(true);
          setError("");
          let savedLlm: LlmSettings | null = null;
          try {
            savedLlm = await llm.save();
          } catch {
            // The section shows the server's (Turkish) reason inline.
            setSaving(false);
            return;
          }
          try {
            await onSave({ ...draft, backend: normalizeBackend(draft.backend) });
            if (savedLlm) onLlmSaved?.(savedLlm);
            onClose();
          } catch (cause) {
            if (savedLlm) onLlmSaved?.(savedLlm);
            setError(
              cause instanceof Error ? cause.message : "Ayarlar kaydedilemedi.",
            );
          } finally {
            setSaving(false);
          }
        }}
      >
        <LlmSection llm={llm} focusKey={focusKey} />
        <DesktopSection backend={current.backend} />
        <label className="setting-row">
          <span>
            <strong>Sesli yanıt</strong>
            <small>Cevapları Türkçe sesli oku.</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            className="switch"
            checked={draft.speak}
            onChange={(event) =>
              setDraft({ ...draft, speak: event.target.checked })
            }
          />
        </label>
        <button
          type="button"
          className="disclosure"
          aria-expanded={showAdvanced}
          aria-controls="settings-advanced"
          onClick={() => setAdvanced(!showAdvanced)}
        >
          <span>
            <strong>Gelişmiş</strong>
            <small>Sunucu adresi, web arama anahtarı</small>
          </span>
          <ChevronDown size={16} className={showAdvanced ? "rotated" : ""} />
        </button>
        {showAdvanced && (
        <div id="settings-advanced" className="advanced">
        <section className="settings-group">
          <label className="field-label" htmlFor="backend">
            Sunucu adresi
          </label>
          <div className="field-with-action">
            <input
              id="backend"
              type="url"
              inputMode="url"
              spellCheck={false}
              autoComplete="off"
              value={draft.backend}
              disabled={busy}
              aria-invalid={Boolean(urlError)}
              aria-describedby="backend-help"
              onChange={(event) => {
                setDraft({ ...draft, backend: event.target.value });
                setTest({ kind: "idle" });
              }}
              placeholder="http://localhost:8765"
            />
            <button
              type="button"
              className="secondary compact"
              disabled={Boolean(urlError) || test.kind === "testing"}
              onClick={() => void runTest()}
            >
              {test.kind === "testing" ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <PlugZap size={14} />
              )}
              Sunucuyu test et
            </button>
          </div>
          <p id="backend-help" className={urlError ? "field-error" : "field-help"}>
            {urlError ??
              (remote
                ? "Uyarı: uzantı güvenlik politikası yalnızca localhost ve 127.0.0.1 adreslerine izin verir."
                : "Polaris sunucusunun (FastAPI) adresi.")}
          </p>
          {test.kind !== "idle" && test.kind !== "testing" && (
            <p className={`test-result ${test.kind}`} role="status">
              {test.kind === "ok" ? (
                <Check size={13} />
              ) : (
                <TriangleAlert size={13} />
              )}
              {test.text}
            </p>
          )}
        </section>
        {llm.load.kind === "ready" && (
          <section className="settings-group">
            <label className="field-label" htmlFor="brave-key">
              Brave Search anahtarı{" "}
              <span className="optional">isteğe bağlı</span>
            </label>
            <input
              id="brave-key"
              type="password"
              className="text-input mono"
              spellCheck={false}
              autoComplete="off"
              placeholder={
                llm.load.saved.brave_key_set
                  ? "Kayıtlı · değiştirmek için yenisini yaz"
                  : "BSA…"
              }
              value={llm.braveKey}
              onChange={(event) => llm.setBraveKey(event.target.value.trim())}
            />
            <p className="field-help">
              {llm.load.saved.brave_key_set
                ? "Web arama Brave API ile yapılıyor."
                : "Boşsa web arama anahtarsız DuckDuckGo ile yapılır."}
            </p>
          </section>
        )}
        </div>
        )}
        {busy && (
          <p className="field-help">
            Bağlantı ayarlarını değiştirmek için mevcut görevin bitmesini bekle.
          </p>
        )}
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        <div className="settings-footer">
          <button
            className="primary full"
            type="submit"
            disabled={saving || Boolean(urlError)}
          >
            {saving ? (
              <>
                <LoaderCircle size={15} className="spin" /> Kaydediliyor…
              </>
            ) : (
              "Kaydet"
            )}
          </button>
          <p className="version-line">Polaris sürüm {extensionVersion()}</p>
        </div>
      </form>
    </Modal>
  );
}

/** First-run / health cards shown above the conversation in live mode. */
export function HealthCard({
  health,
  backend,
  checking,
  onRetry,
  onEnterKey,
}: {
  health: HealthState;
  backend: string;
  checking: boolean;
  onRetry: () => void;
  /** Opens Ayarlar focused on the API key field. */
  onEnterKey?: () => void;
}) {
  const retry = (
    <button className="secondary compact" onClick={onRetry} disabled={checking}>
      <RefreshCw size={14} className={checking ? "spin" : ""} />
      {checking ? "Kontrol ediliyor…" : "Tekrar dene"}
    </button>
  );
  if (health.kind === "unreachable")
    return (
      <section className="health-card danger" aria-labelledby="health-title">
        <div className="health-head">
          <span className="health-icon">
            <ServerOff size={18} />
          </span>
          <div>
            <h3 id="health-title">Polaris sunucusu çalışmıyor</h3>
            <p>
              {backend} adresine ulaşılamadı. Polaris tepsi uygulamasını aç ya
              da repo kökünde şunu çalıştır:
            </p>
          </div>
        </div>
        <div className="command">
          <code>{RUN_BACKEND_COMMAND}</code>
          <CopyButton
            value={RUN_BACKEND_COMMAND}
            label="Komutu kopyala"
            className="copy-command"
          />
        </div>
        <p className="health-note">
          {health.message} Geliştirici kurulumunda alternatif:{" "}
          <code>{RUN_BACKEND_SCRIPT}</code>. Sunucu açılınca bu kart kendiliğinden
          kaybolur.
        </p>
        <div className="health-actions">
          {retry}
        </div>
      </section>
    );
  if (health.kind === "ok" && health.health.llm_configured === false)
    return (
      <section className="health-card warning" aria-labelledby="health-title">
        <div className="health-head">
          <span className="health-icon">
            <ShieldAlert size={18} />
          </span>
          <div>
            <h3 id="health-title">Yapay zekâ anahtarı ayarlı değil</h3>
            <p>
              Polaris’in düşünebilmesi için bir yapay zekâ anahtarı gerekiyor.
              Anahtarını yapıştır, modelini seç — yeniden başlatma gerekmez.
            </p>
          </div>
        </div>
        <div className="health-actions">
          {onEnterKey && (
            <button className="primary compact" onClick={onEnterKey}>
              <KeyRound size={14} /> Anahtarı gir
            </button>
          )}
          {retry}
        </div>
        <p className="health-note">
          Geliştiriciysen alternatif: <code>backend/.env</code> içine{" "}
          <code>DEEPSEEK_API_KEY</code> yazıp sunucuyu yeniden başlat.
        </p>
      </section>
    );
  return null;
}

const onOff = (value: boolean | null, on: string, off: string) =>
  value === null ? null : value ? on : off;

export function StatusPopover({
  settings,
  status,
  health,
  checking,
  onRecheck,
  onClose,
}: {
  settings: Settings;
  status: ConnectionStatus;
  health: HealthState;
  checking: boolean;
  onRecheck: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const down = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        !ref.current?.contains(target) &&
        !(target instanceof Element && target.closest(".connection"))
      )
        closeRef.current();
    };
    document.addEventListener("mousedown", down);
    return () => document.removeEventListener("mousedown", down);
  }, []);
  const info = health.kind === "ok" ? health.health : null;
  const modelName = info?.llm_model ? (
    <span className="model-value">
      {prettyModel(info.llm_model)}
      {prettyModel(info.llm_model) !== info.llm_model && (
        <code>{info.llm_model}</code>
      )}
    </span>
  ) : info ? (
    "—"
  ) : null;
  const rows: Array<[string, ReactNode]> = [
        ["Sunucu", settings.backend],
        [
          "Bağlantı",
          { connected: "Bağlı", connecting: "Bağlanıyor", disconnected: "Kopuk" }[
            status
          ],
        ],
        [
          "Sağlık",
          health.kind === "ok"
            ? "Çalışıyor"
            : health.kind === "checking"
              ? "Kontrol ediliyor"
              : "Ulaşılamıyor",
        ],
        ["Model", modelName],
        ["Yapay zekâ", info ? onOff(info.llm_configured, "Hazır", "Anahtar yok") : null],
        ["Masaüstü (CUA)", info ? onOff(info.cua_enabled, "Açık", "Kapalı") : null],
        ["Ses tanıma", info ? onOff(info.stt_ready, "Hazır", "Hazır değil") : null],
        ["Brave arama", info ? onOff(info.brave_configured, "Ayarlı", "Anahtar yok") : null],
        ["Sunucu sürümü", info?.version ?? null],
        ["Uzantı", extensionVersion()],
  ];
  return (
    <div
      ref={ref}
      className="status-popover"
      role="dialog"
      aria-label="Bağlantı durumu"
    >
      <dl>
        {rows
          .filter((row) => row[1] !== null)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      <button
        className="secondary compact full-width"
        onClick={onRecheck}
        disabled={checking}
      >
        <RefreshCw size={13} className={checking ? "spin" : ""} /> Yeniden
        kontrol et
      </button>
    </div>
  );
}

export function ErrorNote({ error }: { error: FriendlyError }) {
  return (
    <div className="error-card" role="alert">
      <TriangleAlert size={15} />
      <div>
        <strong>{error.title}</strong>
        <p>{error.hint}</p>
        {error.detail && error.detail !== error.title && (
          <details>
            <summary>Ayrıntı</summary>
            <p className="error-detail">{error.detail}</p>
          </details>
        )}
      </div>
    </div>
  );
}

export function Sources({
  citations,
  highlight,
}: {
  citations: Citation[];
  highlight: (ref: number) => void;
}) {
  if (!citations.length) return null;
  return (
    <div className="sources">
      <span className="eyebrow">KAYNAKLAR</span>
      <div className="chips">
        {citations.map((citation, index) => (
          <span className="source-chip" key={index}>
            <span className="source-index">{index + 1}</span>
            {safeUrl(citation.url) ? (
              <a
                href={safeUrl(citation.url)}
                target="_blank"
                rel="noopener noreferrer"
              >
                {citation.title}
                <ExternalLink size={11} />
              </a>
            ) : (
              <span>{citation.title}</span>
            )}
            {citation.ref != null && (
              <button
                onClick={() => highlight(citation.ref!)}
                aria-label={`${citation.title}: öğe ${citation.ref} göster`}
              >
                #{citation.ref}
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

const agentNames: Record<TraceStep["agent"], string> = {
  router: "Yönlendirici",
  planner: "Planlayıcı",
  executor: "Uygulayıcı",
  judge: "Denetçi",
  responder: "Yanıtlayıcı",
  system: "Sistem",
};

export function Timeline({
  steps,
  backend,
  highlight,
  live,
}: {
  steps: TraceStep[];
  backend: string;
  highlight: (ref: number) => void;
  live: boolean;
}) {
  const [expanded, setExpanded] = useState(live);
  // Open while the agents work; fold away once the answer arrives so it leads.
  useEffect(() => {
    if (!live) setExpanded(false);
  }, [live]);
  const [image, setImage] = useState<string | null>(null);
  const failures = steps.filter((step) => isToolFailure(step.decision)).length;
  const totalMs = steps.reduce((sum, step) => sum + step.latency_ms, 0);
  return (
    <section className={`timeline ${live ? "live" : ""}`}>
      <button
        className="timeline-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <span>
          <span className="sparkle">
            <PolarisMark size={13} />
          </span>{" "}
          Neden? <small>{steps.length} adım</small>
          {failures > 0 && (
            <small className="failure-count">{failures} araç hatası</small>
          )}
        </span>
        <span className="timeline-meta">
          {totalMs > 0 && <small>{(totalMs / 1000).toFixed(1)} sn</small>}
          <ChevronDown size={16} className={expanded ? "rotated" : ""} />
        </span>
      </button>
      {expanded && (
        <ol className="timeline-steps">
          {steps.length === 0 ? (
            <li className="muted small waiting">İlk adım bekleniyor…</li>
          ) : (
            steps.map((step, index) => {
              const failed = isToolFailure(step.decision);
              return (
                <li
                  className={`trace-step agent-line-${step.agent} ${failed ? "failed" : ""}`}
                  key={`${step.ts}-${index}`}
                >
                  <div className="trace-heading">
                    <span className={`agent-badge agent-${step.agent}`}>
                      {agentNames[step.agent]}
                    </span>
                    {failed && (
                      <span className="failed-badge">Araç başarısız</span>
                    )}
                    <small>
                      {step.latency_ms} ms
                      {step.tokens != null ? ` · ${step.tokens} token` : ""}
                    </small>
                  </div>
                  <strong>{step.decision}</strong>
                  {step.why && step.why !== step.decision && <p>{step.why}</p>}
                  {step.confidence !== null && (
                    <div className="confidence">
                      <div
                        role="progressbar"
                        aria-label="Güven"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(step.confidence * 100)}
                      >
                        <i style={{ width: `${step.confidence * 100}%` }} />
                      </div>
                      <span>%{Math.round(step.confidence * 100)} güven</span>
                    </div>
                  )}
                  {step.tool && (
                    <code className="tool-code">
                      {step.tool.name}(
                      {Object.entries(step.tool.args)
                        .map(
                          ([key, value]) => `${key}=${JSON.stringify(value)}`,
                        )
                        .join(", ")}
                      )
                    </code>
                  )}
                  {step.scores && (
                    <div className="scores">
                      <span>
                        Doğruluk <b>{step.scores.correct}/5</b>
                      </span>
                      <span>
                        Tamlık <b>{step.scores.complete}/5</b>
                      </span>
                      <span>
                        Güvenlik <b>{step.scores.safe}/5</b>
                      </span>
                    </div>
                  )}
                  {step.verdict && (
                    <span className={`verdict ${step.verdict}`}>
                      {
                        {
                          pass: "Geçti",
                          revise: "Düzeltme",
                          ask_user: "Onay bekliyor",
                        }[step.verdict]
                      }
                    </span>
                  )}
                  {step.evidence.length > 0 && (
                    <div className="chips">
                      {step.evidence.map((evidence, position) => {
                        if (/^ref:\d+$/.test(evidence))
                          return (
                            <button
                              className="chip"
                              key={position}
                              onClick={() =>
                                highlight(Number(evidence.slice(4)))
                              }
                            >
                              Öğe #{evidence.slice(4)}
                            </button>
                          );
                        if (
                          evidence.startsWith("url:") &&
                          safeUrl(evidence.slice(4))
                        )
                          return (
                            <a
                              className="chip"
                              key={position}
                              href={safeUrl(evidence.slice(4))}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              Kaynak <ExternalLink size={11} />
                            </a>
                          );
                        const shot = evidence.startsWith("shot:")
                          ? shotUrl(evidence.slice(5), backend)
                          : undefined;
                        if (shot)
                          return (
                            <button
                              className="shot-thumb"
                              key={position}
                              onClick={() => setImage(shot)}
                            >
                              <img src={shot} alt="Masaüstü kanıtını büyüt" />
                            </button>
                          );
                        return (
                          <span className="evidence-text" key={position}>
                            {evidence}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </li>
              );
            })
          )}
        </ol>
      )}
      {image && (
        <Modal title="Masaüstü kanıtı" onClose={() => setImage(null)}>
          <img
            className="shot-full"
            src={image}
            alt="Masaüstü ekran görüntüsü"
          />
        </Modal>
      )}
    </section>
  );
}
