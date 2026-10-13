import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  CircleStop,
  LoaderCircle,
  Mic,
  Settings as SettingsIcon,
  ShieldCheck,
  Trash2,
  Volume2,
  X,
} from "lucide-react";
import { executeBrowserTool } from "../bridge/executeTool";
import {
  ConfirmModal,
  CopyButton,
  ErrorNote,
  HealthCard,
  Markdown,
  PolarisMark,
  SettingsModal,
  Sources,
  StatusPopover,
  Timeline,
} from "./components";
import { friendlyError, type FriendlyError } from "./errors";
import { checkHealth, type HealthState } from "./health";
import {
  normalizeBackend,
  requestId,
  type Citation,
  type Confirmation,
  type ConnectionStatus,
  type ServerEvent,
  type TabContext,
  type TraceStep,
} from "./protocol";
import {
  defaultSettings,
  loadSettings,
  saveSettings,
  type Settings,
} from "./settings";
import { useRecorder, useSpeaker } from "./voice";
import { connectSocket, type OnEvent, type OnStatus, type Transport } from "./ws";

interface Request {
  id: string;
  text: string;
  source: "text" | "voice";
  tab: TabContext | null;
  steps: TraceStep[];
  answer: string;
  spoken: string;
  citations: Citation[];
  status: "running" | "done" | "cancelled" | "error";
  error?: FriendlyError;
}
interface Pending {
  event: Confirmation;
  deadline: number;
}

const suggestions = [
  {
    label: "Bu sayfayı özetle",
    text: "Bu sayfayı özetle",
    detail: "Önemli noktaları öğren",
    icon: "01",
  },
  {
    label: "Brave’de araştır",
    text: "Brave’de en iyi 3 kablosuz kulaklığı ara, karşılaştır",
    detail: "Kaynaklarla karşılaştır",
    icon: "02",
  },
  {
    label: "Yarın 10’da hatırlat",
    text: "Yarın saat 10’da toplantıyı hatırlat",
    detail: "Gününü kolaylaştır",
    icon: "03",
  },
  {
    label: "Bu formu doldur",
    text: "Bu formu doldur ve gönder",
    detail: "Göndermeden önce onay iste",
    icon: "04",
  },
];

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/** Opens the server connection. Tests inject a scripted transport here. */
export type TransportFactory = (
  backend: string,
  onEvent: OnEvent,
  onStatus: OnStatus,
  onError: (message: string) => void,
) => Transport;

export default function App({
  createTransport = connectSocket,
}: { createTransport?: TransportFactory } = {}) {
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [ready, setReady] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showStatus, setShowStatus] = useState(false);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const statusRef = useRef(status);
  const [health, setHealth] = useState<HealthState>({ kind: "checking" });
  const [healthChecking, setHealthChecking] = useState(false);
  const healthSeq = useRef(0);
  const [requests, setRequests] = useState<Request[]>([]);
  const requestsRef = useRef(requests);
  requestsRef.current = requests;
  const [draft, setDraft] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const activeRef = useRef<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const [error, setError] = useState("");
  const [notification, setNotification] = useState("");
  const [hint, setHint] = useState("");
  const transport = useRef<Transport | null>(null);
  const handledCalls = useRef(new Set<string>());
  const generation = useRef(0);
  const scrollEnd = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const reportError = useCallback((message: string) => setError(message), []);
  const speaker = useSpeaker(settings.backend, reportError);
  const speakerRef = useRef(speaker);
  speakerRef.current = speaker;

  const runHealth = useCallback(async () => {
    const sequence = ++healthSeq.current;
    setHealthChecking(true);
    const result = await checkHealth(settingsRef.current.backend);
    if (sequence !== healthSeq.current) return;
    setHealthChecking(false);
    setHealth(result);
  }, []);

  const activate = (id: string | null) => {
    activeRef.current = id;
    setActiveId(id);
  };
  const update = useCallback(
    (id: string, change: (request: Request) => Request) => {
      const next = requestsRef.current.map((request) =>
        request.id === id ? change(request) : request,
      );
      requestsRef.current = next;
      setRequests(next);
    },
    [],
  );
  const clearPending = () => {
    pendingRef.current = null;
    setPending(null);
  };
  const decide = (approved: boolean) => {
    const value = pendingRef.current;
    if (!value) return;
    clearPending();
    const allowed = approved && Date.now() < value.deadline;
    if (
      !transport.current?.send({
        type: "confirm",
        call_id: value.event.call_id,
        approved: allowed,
      })
    ) {
      reportError("Onay cevabı gönderilemedi. Bağlantıyı kontrol et.");
    }
  };

  const handleEvent = async (event: ServerEvent) => {
    if (event.type === "notify") {
      setNotification(event.text);
      if (settingsRef.current.speak)
        void speakerRef.current.speak(event.spoken);
      return;
    }
    if (event.type === "error") {
      if (!event.request_id) {
        const friendly = friendlyError(event.message);
        reportError(`${friendly.title}. ${friendly.hint}`);
        return;
      }
      if (event.request_id !== activeRef.current) return;
      update(event.request_id, (request) => ({
        ...request,
        status: "error",
        error: friendlyError(event.message),
      }));
      clearPending();
      activate(null);
      return;
    }
    if (event.request_id !== activeRef.current) return;
    if (event.type === "trace")
      update(event.request_id, (request) => ({
        ...request,
        steps: [...request.steps, event.step],
      }));
    if (event.type === "final") {
      update(event.request_id, (request) => ({
        ...request,
        status: "done",
        answer: event.answer,
        spoken: event.spoken,
        citations: event.citations,
      }));
      clearPending();
      activate(null);
      if (settingsRef.current.speak)
        void speakerRef.current.speak(event.spoken);
    }
    if (event.type === "need_confirm") {
      if (pendingRef.current?.event.call_id === event.call_id) return;
      if (pendingRef.current) {
        transport.current?.send({
          type: "confirm",
          call_id: event.call_id,
          approved: false,
        });
        return;
      }
      const value = { event, deadline: Date.now() + 60000 };
      pendingRef.current = value;
      setPending(value);
    }
    if (event.type === "tool_call") {
      const callKey = `${event.request_id}:${event.call_id}`;
      if (handledCalls.current.has(callKey)) return;
      handledCalls.current.add(callKey);
      const callGeneration = generation.current;
      const tab = requestsRef.current.find(
        (request) => request.id === event.request_id,
      )?.tab;
      let result: { ok: boolean; data: unknown | null; error: string | null };
      try {
        if (!event.name.startsWith("browser."))
          throw new Error("Panel yalnızca browser.* araçlarını çalıştırır.");
        if (!tab) throw new Error("Bu görev için aktif sekme bulunamadı.");
        result = await executeBrowserTool(event.name, event.args, tab.id);
      } catch (cause) {
        result = {
          ok: false,
          data: null,
          error:
            cause instanceof Error
              ? cause.message
              : "Tarayıcı işlemi başarısız.",
        };
      }
      if (
        generation.current === callGeneration &&
        activeRef.current === event.request_id
      ) {
        if (
          !transport.current?.send({
            type: "tool_result",
            call_id: event.call_id,
            ...result,
          })
        )
          reportError("Araç sonucu gönderilemedi.");
      }
    }
  };
  const handlerRef = useRef(handleEvent);
  handlerRef.current = handleEvent;

  useEffect(() => {
    let cancelled = false;
    void loadSettings().then((saved) => {
      if (!cancelled) {
        setSettings(saved);
        setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  // First run / backend change: check /health before the user types.
  useEffect(() => {
    if (!ready) return;
    healthSeq.current += 1;
    setHealthChecking(false);
    setHealth({ kind: "checking" });
    void runHealth();
  }, [ready, settings.backend, runHealth]);
  useEffect(() => {
    if (!ready) return;
    generation.current += 1;
    const sessionGeneration = generation.current;
    handledCalls.current.clear();
    statusRef.current = "connecting";
    const onEvent = (event: ServerEvent) => {
      if (generation.current === sessionGeneration)
        void handlerRef.current(event);
    };
    const onStatus = (next: ConnectionStatus) => {
      if (generation.current !== sessionGeneration) return;
      const previous = statusRef.current;
      statusRef.current = next;
      setStatus(next);
      // Re-check health on (re)connect and whenever a live session drops.
      if (
        (next === "connected" ||
          (next === "disconnected" && previous === "connected"))
      )
        void runHealth();
      if (next === "disconnected" && activeRef.current) {
        update(activeRef.current, (request) => ({
          ...request,
          status: "error",
          error: {
            title: "Sunucu bağlantısı koptu",
            hint: "Görev otomatik tekrar edilmedi; bağlantı gelince yeniden gönderebilirsin.",
            detail: "",
          },
        }));
        clearPending();
        activate(null);
      }
    };
    try {
      transport.current = createTransport(
        settings.backend,
        onEvent,
        onStatus,
        reportError,
      );
    } catch {
      setStatus("disconnected");
      reportError("Backend adresine bağlanılamadı. Ayarları kontrol et.");
    }
    return () => {
      generation.current += 1;
      transport.current?.close();
      transport.current = null;
    };
  }, [ready, settings.backend, reportError, update, runHealth, createTransport]);
  // While the server is down the socket keeps retrying; poll /health too so the
  // card clears on its own as soon as the backend starts.
  useEffect(() => {
    if (health.kind !== "unreachable") return;
    const timer = setInterval(() => void runHealth(), 5000);
    return () => clearInterval(timer);
  }, [health.kind, runHealth]);
  useEffect(() => {
    if (!settings.speak) speakerRef.current.stop();
  }, [settings.speak]);

  const applySettings = async (next: Settings) => {
    const normalized = { ...next, backend: normalizeBackend(next.backend) };
    if (
      busyRef.current &&
      normalized.backend !== settingsRef.current.backend
    )
      throw new Error("Önce mevcut görevi bitir veya iptal et.");
    await saveSettings(normalized);
    speakerRef.current.stop();
    setSettings(normalized);
    setError("");
  };

  const sendMessage = async (
    text: string,
    source: "text" | "voice" = "text",
  ) => {
    const clean = text.trim();
    if (!clean || activeRef.current) return;
    if (status !== "connected") {
      reportError(
        "Sunucuya bağlı değilsin. Sunucuyu başlat veya Ayarlar’dan demo modunu aç.",
      );
      return;
    }
    speakerRef.current.stop();
    setError("");
    setHint("");
    const id = requestId();
    activate(id);
    const request: Request = {
      id,
      text: clean,
      source,
      tab: null,
      steps: [],
      answer: "",
      spoken: "",
      citations: [],
      status: "running",
    };
    const next = [...requestsRef.current, request];
    requestsRef.current = next;
    setRequests(next);
    setDraft("");
    let tab: TabContext | null = null;
    try {
      if (typeof chrome !== "undefined" && chrome.tabs?.query) {
        // The panel may live in its own tab/window: target the page the user
        // is looking at (active web tab first, else the most recent web tab).
        const isWeb = (t: chrome.tabs.Tab) => /^https?:/i.test(t.url ?? "");
        const [active] = await chrome.tabs.query({
          active: true,
          lastFocusedWindow: true,
        });
        const current =
          active && isWeb(active)
            ? active
            : (await chrome.tabs.query({}))
                .filter(isWeb)
                .sort((a, b) => (b.lastAccessed ?? 0) - (a.lastAccessed ?? 0))[0];
        if (current?.id !== undefined)
          tab = {
            id: current.id,
            url: current.url ?? "",
            title: current.title ?? "",
          };
      }
    } catch {
      /* Text-only backend tasks still work without an active browser tab. */
    }
    if (activeRef.current !== id) return;
    update(id, (item) => ({ ...item, tab }));
    if (
      !transport.current?.send({
        type: "user_msg",
        id,
        text: clean,
        source,
        tab,
      })
    ) {
      update(id, (item) => ({
        ...item,
        status: "error",
        error: {
          title: "Mesaj gönderilemedi",
          hint: "Bağlantı kurulduktan sonra tekrar dene.",
          detail: "",
        },
      }));
      activate(null);
    }
  };
  const sendRef = useRef(sendMessage);
  sendRef.current = sendMessage;
  const recorder = useRecorder(
    settings.backend,
    (text) => {
      setHint(`Ses komutu: ${text}`);
      void sendRef.current(text, "voice");
    },
    reportError,
  );
  const recorderRef = useRef(recorder);
  recorderRef.current = recorder;
  const busy = activeId !== null || recorder.state !== "idle";
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const cancel = () => {
    const id = activeRef.current;
    if (!id) return;
    if (pendingRef.current) decide(false);
    transport.current?.send({ type: "cancel", id });
    update(id, (request) => ({ ...request, status: "cancelled" }));
    activate(null);
    speakerRef.current.stop();
  };
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;
  const showStatusRef = useRef(showStatus);
  showStatusRef.current = showStatus;
  const clearChat = () => {
    if (busyRef.current) return;
    speakerRef.current.stop();
    requestsRef.current = [];
    setRequests([]);
    setError("");
    setHint("");
    setNotification("");
    composer.current?.focus();
  };
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Open dialogs handle Esc themselves (close / reject).
        if (document.querySelector("dialog[open]")) return;
        if (showStatusRef.current) {
          setShowStatus(false);
          return;
        }
        if (["recording", "starting"].includes(recorderRef.current.state)) {
          recorderRef.current.stop();
          return;
        }
        if (activeRef.current) {
          event.preventDefault();
          cancelRef.current();
        } else if (speakerRef.current.playing) speakerRef.current.stop();
        return;
      }
      const target = event.target;
      const interactive =
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          Boolean(target.closest("input,textarea,button,select,a,dialog")));
      if (
        event.code !== "Space" ||
        event.repeat ||
        interactive ||
        busyRef.current ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey
      )
        return;
      event.preventDefault();
      void recorderRef.current.start();
    };
    const keyup = (event: KeyboardEvent) => {
      if (
        event.code === "Space" &&
        ["recording", "starting"].includes(recorderRef.current.state)
      ) {
        event.preventDefault();
        recorderRef.current.stop();
      }
    };
    const blur = () => recorderRef.current.stop();
    window.addEventListener("keydown", keydown);
    window.addEventListener("keyup", keyup);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("keyup", keyup);
      window.removeEventListener("blur", blur);
    };
  }, []);
  useEffect(() => {
    const node = scrollEnd.current;
    if (!node) return;
    const list = node.parentElement;
    if (list && list.scrollHeight - list.scrollTop - list.clientHeight < 350)
      node.scrollIntoView({ block: "end" });
  }, [requests, activeId]);

  const highlight = async (ref: number, tab: TabContext | null) => {
    try {
      if (!tab) throw new Error("Bu cevaba ait sekme bulunamadı.");
      const result = await executeBrowserTool(
        "browser.highlight",
        { ref },
        tab.id,
      );
      if (!result.ok) throw new Error(result.error ?? "Öğe gösterilemedi.");
    } catch (cause) {
      reportError(
        cause instanceof Error ? cause.message : "Öğe gösterilemedi.",
      );
    }
  };

  const serverDown = health.kind === "unreachable";
  const llmMissing =
    health.kind === "ok" && health.health.llm_configured === false;
  const pillState = serverDown
      ? "disconnected"
      : llmMissing
        ? "warning"
        : status;
  const pillText = serverDown
      ? "Sunucu kapalı"
      : llmMissing
        ? "Anahtar yok"
        : {
            connected: "Bağlı",
            connecting: "Bağlanıyor",
            disconnected: "Kopuk",
          }[status];
  const canSend = status === "connected" && !serverDown;

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">
            <PolarisMark size={20} />
          </span>
          <div>
            <h1>Polaris</h1>
            <span className="brand-subtitle">Kişisel asistan</span>
          </div>
        </div>
        <div className="header-actions">
          <button
            className={`connection ${pillState}`}
            aria-expanded={showStatus}
            aria-label={`Bağlantı durumu: ${pillText}`}
            onClick={() => setShowStatus((open) => !open)}
          >
            <i />
            {pillText}
          </button>
          {requests.length > 0 && (
            <button
              className="icon-button"
              aria-label="Sohbeti temizle"
              title="Sohbeti temizle"
              disabled={busy}
              onClick={clearChat}
            >
              <Trash2 size={16} />
            </button>
          )}
          <button
            className="icon-button"
            aria-label="Ayarlar"
            title="Ayarlar"
            onClick={() => setShowSettings(true)}
          >
            <SettingsIcon size={17} />
          </button>
        </div>
        {showStatus && (
          <StatusPopover
            settings={settings}
            status={status}
            health={health}
            checking={healthChecking}
            onRecheck={() => void runHealth()}
            onClose={() => setShowStatus(false)}
          />
        )}
      </header>
      {notification && (
        <div className="notice notification" role="status">
          <span>{notification}</span>
          <button
            className="icon-button"
            aria-label="Bildirimi kapat"
            onClick={() => setNotification("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      <section className="conversation" aria-label="Sohbet">
        <HealthCard
            health={health}
            backend={settings.backend}
            checking={healthChecking}
            onRetry={() => void runHealth()}
          />
        {requests.length === 0 && (
          <div className="welcome">
            <div className="welcome-symbol" aria-hidden="true">
              <span className="halo" />
              <PolarisMark size={30} />
            </div>
            <p className="eyebrow">Yolunu bulan asistan</p>
            <h2>Ne yapmak istersin?</h2>
            <p className="welcome-copy">
              Bir şey sor, sayfayı keşfet veya bir işi bana bırak. Her adımın
              gerekçesini görürsün.
            </p>
            <div className="suggestions">
              {suggestions.map((item) => (
                <button
                  key={item.icon}
                  disabled={busy || !canSend}
                  onClick={() => void sendMessage(item.text)}
                >
                  <span className="suggestion-number">{item.icon}</span>
                  <span className="suggestion-text">
                    <strong>{item.label}</strong>
                    <small>{item.detail}</small>
                  </span>
                  <ArrowUp size={15} className="diagonal-arrow" />
                </button>
              ))}
            </div>
            <p className="welcome-foot">
              <ShieldCheck size={13} /> Önemli işlemler senin onayınla yapılır.
            </p>
          </div>
        )}
        {requests.map((request) => (
          <article className="exchange" key={request.id}>
            <div className="user-message">
              <span className="message-label">
                Sen {request.source === "voice" && <Mic size={11} />}
              </span>
              <p>{request.text}</p>
            </div>
            <div className="assistant-message">
              <div className="assistant-label">
                <span className="mini-mark">
                  <PolarisMark size={12} />
                </span>
                Polaris
                {request.status === "running" && (
                  <LoaderCircle className="spin" size={13} />
                )}
              </div>
              {request.answer && <Markdown text={request.answer} />}
              {request.status === "running" && !request.answer && (
                <p className="working">
                  <span className="dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  {request.steps.at(-1)?.decision ?? "İsteğini alıyorum…"}
                </p>
              )}
              {request.status === "error" && request.error && (
                <ErrorNote error={request.error} />
              )}
              {request.status === "cancelled" && (
                <p className="muted small">Görev iptal edildi.</p>
              )}
              <Sources
                citations={request.citations}
                highlight={(ref) => void highlight(ref, request.tab)}
              />
              {(request.steps.length > 0 || request.status === "running") && (
                <Timeline
                  steps={request.steps}
                  backend={settings.backend}
                  highlight={(ref) => void highlight(ref, request.tab)}
                  live={request.status === "running"}
                />
              )}
              {(request.answer || request.spoken) && (
                <div className="answer-actions">
                  {request.answer && (
                    <CopyButton value={request.answer} label="Cevabı kopyala" />
                  )}
                  {request.spoken && (
                    <button
                      className="text-button"
                      onClick={() =>
                        void speaker.speak(request.spoken)
                      }
                    >
                      <Volume2 size={13} /> Sesli oku
                    </button>
                  )}
                </div>
              )}
            </div>
          </article>
        ))}
        <div ref={scrollEnd} />
      </section>
      <footer className="composer-area">
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="Hatayı kapat"
              onClick={() => setError("")}
            >
              <X size={14} />
            </button>
          </div>
        )}
        {hint && (
          <div className="hint" role="status">
            {hint}
          </div>
        )}
        {recorder.permissionNeeded && (
          <button
            className="text-button"
            onClick={() =>
              void recorder
                .openPermissionPage()
                .catch(() => reportError("İzin sekmesi açılamadı."))
            }
          >
            Mikrofon izni ver
          </button>
        )}
        {(activeId || speaker.playing) && (
          <div className="task-actions">
            {activeId && (
              <button
                className="cancel-task"
                aria-label="Görevi iptal et"
                onClick={cancel}
              >
                <CircleStop size={14} /> Görevi iptal et
                <kbd>Esc</kbd>
              </button>
            )}
            {speaker.playing && (
              <button className="cancel-task" onClick={speaker.stop}>
                <CircleStop size={14} /> Sesli okumayı durdur
              </button>
            )}
          </div>
        )}
        <form
          className={`composer ${recorder.state === "recording" ? "recording" : ""}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (recorder.state === "idle") void sendMessage(draft);
          }}
        >
          <textarea
            ref={composer}
            rows={2}
            maxLength={12000}
            aria-label="Mesajın"
            placeholder="Bir şey sor veya bir iş ver…"
            value={draft}
            disabled={!ready}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                if (!busy) void sendMessage(draft);
              }
            }}
          />
          <div className="composer-controls">
            <button
              type="button"
              className={`mic-button ${recorder.state === "recording" ? "is-recording" : ""}`}
              disabled={
                !ready || activeId !== null || recorder.state === "transcribing"
              }
              aria-label="Basılı tut ve konuş"
              title="Basılı tut ve konuş"
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.currentTarget.setPointerCapture(event.pointerId);
                void recorder.start();
              }}
              onPointerUp={recorder.stop}
              onPointerCancel={recorder.stop}
              onLostPointerCapture={recorder.stop}
              onKeyDown={(event) => {
                if (
                  (event.key === " " || event.key === "Enter") &&
                  !event.repeat
                ) {
                  event.preventDefault();
                  void recorder.start();
                }
              }}
              onKeyUp={(event) => {
                if (event.key === " " || event.key === "Enter") {
                  event.preventDefault();
                  recorder.stop();
                }
              }}
            >
              <Mic size={17} />
            </button>
            <span className="input-hint" role="status">
              {recorder.state === "recording" ? (
                <>
                  <span className="rec-dot" aria-hidden="true" />
                  <span className="rec-time">{clock(recorder.elapsed)}</span>
                  Dinliyorum… bırakınca gönder
                </>
              ) : (
                {
                  idle: "Bas-konuş · Space",
                  starting: "Mikrofon açılıyor…",
                  transcribing: "Ses yazıya çevriliyor…",
                }[recorder.state]
              )}
            </span>
            <button
              className="send-button"
              type="submit"
              disabled={!draft.trim() || busy || !canSend}
              aria-label="Mesajı gönder"
            >
              <ArrowUp size={18} />
            </button>
          </div>
        </form>
        <p className="footer-note">
          Shift+Enter yeni satır · Esc iptal · Önemli işlemler onayına sunulur
        </p>
      </footer>
      {showSettings && (
        <SettingsModal
          current={settings}
          busy={busy}
          onClose={() => setShowSettings(false)}
          onSave={applySettings}
        />
      )}
      {pending && (
        <ConfirmModal
          key={pending.event.call_id}
          pending={pending.event}
          deadline={pending.deadline}
          onDecide={decide}
        />
      )}
    </main>
  );
}
