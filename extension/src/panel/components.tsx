import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, ExternalLink, ShieldCheck, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import {
  safeUrl,
  shotUrl,
  type Citation,
  type Confirmation,
  type TraceStep,
} from "./protocol";
import type { Settings } from "./settings";

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
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
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
      className="modal"
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
  return (
    <Modal title="Onayın gerekiyor" onClose={() => onDecide(false)}>
      <div className="approval-symbol">
        <ShieldCheck size={28} />
      </div>
      <p>{pending.summary}</p>
      <div className="tool-code">
        {pending.tool}
        <pre>{JSON.stringify(pending.args, null, 2)}</pre>
      </div>
      <p className="muted small" role="timer">
        {remaining} saniye içinde cevap verilmezse işlem reddedilir.
      </p>
      <div className="button-row">
        <button className="secondary" onClick={() => onDecide(false)}>
          Reddet
        </button>
        <button className="primary" onClick={() => onDecide(true)}>
          <Check size={16} /> Onayla
        </button>
      </div>
    </Modal>
  );
}

export function SettingsModal({
  current,
  busy,
  onSave,
  onClose,
}: {
  current: Settings;
  busy: boolean;
  onSave: (settings: Settings) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(current);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  return (
    <Modal title="Ayarlar" onClose={onClose}>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setSaving(true);
          setError("");
          try {
            await onSave(draft);
            onClose();
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : "Ayarlar kaydedilemedi.",
            );
          } finally {
            setSaving(false);
          }
        }}
      >
        <label className="field-label" htmlFor="backend">
          Backend adresi
        </label>
        <input
          id="backend"
          type="url"
          required
          value={draft.backend}
          disabled={busy}
          onChange={(event) =>
            setDraft({ ...draft, backend: event.target.value })
          }
          placeholder="http://localhost:8000"
        />
        <p className="muted small">Panelin bağlanacağı FastAPI servisi.</p>
        <label className="setting-row">
          <span>
            <strong>Mock modu</strong>
            <small>Örnek verilerle paneli dene.</small>
          </span>
          <input
            type="checkbox"
            checked={draft.mock}
            disabled={busy}
            onChange={(event) =>
              setDraft({ ...draft, mock: event.target.checked })
            }
          />
        </label>
        <label className="setting-row">
          <span>
            <strong>Sesli yanıt</strong>
            <small>Cevapları Türkçe sesli oku.</small>
          </span>
          <input
            type="checkbox"
            checked={draft.speak}
            onChange={(event) =>
              setDraft({ ...draft, speak: event.target.checked })
            }
          />
        </label>
        {busy && (
          <p className="muted small">
            Bağlantı ayarlarını değiştirmek için mevcut görevin bitmesini bekle.
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary full" type="submit" disabled={saving}>
          {saving ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </form>
    </Modal>
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
  const [image, setImage] = useState<string | null>(null);
  return (
    <section className="timeline">
      <button
        className="timeline-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <span>
          <span className="sparkle">✦</span> Neden?{" "}
          <small>{steps.length} adım</small>
        </span>
        <ChevronDown size={16} className={expanded ? "rotated" : ""} />
      </button>
      {expanded && (
        <div className="timeline-steps">
          {steps.length === 0 ? (
            <p className="muted small">İlk adım bekleniyor…</p>
          ) : (
            steps.map((step, index) => (
              <article className="trace-step" key={`${step.ts}-${index}`}>
                <div className="trace-heading">
                  <span className={`agent-badge agent-${step.agent}`}>
                    {step.agent}
                  </span>
                  <small>
                    {step.latency_ms} ms
                    {step.tokens != null ? ` · ${step.tokens} token` : ""}
                  </small>
                </div>
                <strong>{step.decision}</strong>
                <p>{step.why}</p>
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
                      .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
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
                <div className="chips">
                  {step.evidence.map((evidence, position) => {
                    if (/^ref:\d+$/.test(evidence))
                      return (
                        <button
                          className="chip"
                          key={position}
                          onClick={() => highlight(Number(evidence.slice(4)))}
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
              </article>
            ))
          )}
        </div>
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
