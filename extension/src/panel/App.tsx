import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  AudioLines,
  CheckCheck,
  CircleStop,
  LoaderCircle,
  Mic,
  Settings as SettingsIcon,
  ShieldCheck,
  Sparkles,
  Volume2,
  X,
} from "lucide-react";
import { executeBrowserTool } from "virtual:panel-browser-tool";
import { PolarisPet, type PetMood } from "./PolarisPet";
import {
  ConfirmModal,
  Markdown,
  SettingsModal,
  Sources,
  Timeline,
} from "./components";
import { createMockTransport } from "./mocks/session";
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
import { connectSocket, type Transport } from "./ws";

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
  error?: string;
}
interface Pending {
  event: Confirmation;
  deadline: number;
}

export default function App() {
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [ready, setReady] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
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
        void speakerRef.current.speak(event.spoken, settingsRef.current.mock);
      return;
    }
    if (event.type === "error") {
      if (!event.request_id) {
        reportError(event.message);
        return;
      }
      if (event.request_id !== activeRef.current) return;
      update(event.request_id, (request) => ({
        ...request,
        status: "error",
        error: event.message,
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
        void speakerRef.current.speak(event.spoken, settingsRef.current.mock);
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
        if (settingsRef.current.mock)
          result = {
            ok: true,
            data: { text: "Mock sayfa içeriği", mock: true },
            error: null,
          };
        else {
          if (!tab) throw new Error("Bu görev için aktif sekme bulunamadı.");
          result = await executeBrowserTool(event.name, event.args, tab.id);
        }
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
  useEffect(() => {
    if (!ready) return;
    generation.current += 1;
    const sessionGeneration = generation.current;
    handledCalls.current.clear();
    const onEvent = (event: ServerEvent) => {
      if (generation.current === sessionGeneration)
        void handlerRef.current(event);
    };
    const onStatus = (next: ConnectionStatus) => {
      if (generation.current !== sessionGeneration) return;
      setStatus(next);
      if (next === "disconnected" && activeRef.current) {
        update(activeRef.current, (request) => ({
          ...request,
          status: "error",
          error:
            "Bağlantı koptu. Görev otomatik tekrar edilmedi; yeniden gönderebilirsin.",
        }));
        clearPending();
        activate(null);
      }
    };
    try {
      transport.current = settings.mock
        ? createMockTransport(onEvent, onStatus)
        : connectSocket(settings.backend, onEvent, onStatus, reportError);
    } catch {
      setStatus("disconnected");
      reportError("Backend adresine bağlanılamadı. Ayarları kontrol et.");
    }
    return () => {
      generation.current += 1;
      transport.current?.close();
      transport.current = null;
    };
  }, [ready, settings.backend, settings.mock, reportError, update]);
  useEffect(() => {
    if (!settings.speak) speakerRef.current.stop();
  }, [settings.speak]);

  const sendMessage = async (
    text: string,
    source: "text" | "voice" = "text",
  ) => {
    const clean = text.trim();
    if (!clean || activeRef.current) return;
    if (status !== "connected") {
      reportError(
        "Önce backend bağlantısını kur veya ayarlardan mock modunu aç.",
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
        const [current] = await chrome.tabs.query({
          active: true,
          currentWindow: true,
        });
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
        error: "Mesaj gönderilemedi. Bağlantı kurulduktan sonra tekrar dene.",
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
  const petMood: PetMood =
    recorder.state === "recording"
      ? "listening"
      : speaker.playing
        ? "speaking"
        : busy
          ? "thinking"
          : "idle";
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
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
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
    if (requests.length === 0) return;
    const node = scrollEnd.current;
    if (!node) return;
    const list = node.parentElement;
    if (list && list.scrollHeight - list.scrollTop - list.clientHeight < 350)
      node.scrollIntoView({ block: "end" });
  }, [requests, activeId]);

  const highlight = async (ref: number, tab: TabContext | null) => {
    if (settings.mock) {
      setHint(
        `Mock: öğe #${ref} seçildi. Gerçek sayfada değişiklik yapılmadı.`,
      );
      return;
    }
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
  const grantPermission = async () => {
    const path = "src/panel/permission.html";
    if (
      typeof chrome !== "undefined" &&
      chrome.tabs?.create &&
      chrome.runtime?.getURL
    )
      await chrome.tabs.create({ url: chrome.runtime.getURL(path) });
    else window.open(`./permission.html`, "_blank", "noopener");
  };

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="pet-brand">
            <PolarisPet size={44} mood={petMood} />
          </span>
          <div>
            <h1>
              polaris<span className="brand-dot">.</span>
            </h1>
            <span className="brand-subtitle">EVREN ASİSTAN</span>
          </div>
        </div>
        <div className="header-actions">
          <span
            className={`connection ${status}`}
            title={
              settings.mock ? "Örnek veriler kullanılıyor" : settings.backend
            }
          >
            <i />
            {settings.mock
              ? "Mock"
              : {
                  connected: "Bağlı",
                  connecting: "Bağlanıyor",
                  disconnected: "Kopuk",
                }[status]}
          </span>
          <button
            className="icon-button"
            aria-label="Ayarlar"
            onClick={() => setShowSettings(true)}
          >
            <SettingsIcon size={18} />
          </button>
        </div>
      </header>
      <div className="context-bar">
        <span>
          <ShieldCheck size={13} /> Kontrol sende
        </span>
        <span>Ses · Araçlar · Açıklanabilir AI</span>
      </div>
      {settings.mock && (
        <div className="mock-banner">
          <Sparkles size={13} />
          <span>Demo modu · Örnek verilerle çalışıyor</span>
        </div>
      )}
      {!settings.mock && !__BROWSER_BRIDGE_READY__ && (
        <div className="notice small">
          Tarayıcı köprüsü henüz hazır değil. Kişi 2’nin dosyası eklendikten
          sonra yeniden derle.
        </div>
      )}
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
        {requests.length === 0 && (
          <div className="welcome">
            <div className="welcome-symbol">
              <PolarisPet size={84} />
            </div>
            <p className="eyebrow">BİRLİKTE, BİR ADIM İLERİ</p>
            <h2>
              Ne yapmak
              <br />
              istersin?
            </h2>
            <p className="welcome-copy">
              Bir şey sor, sayfayı keşfet veya
              <br />
              bir işi bana bırak.
            </p>
            <div className="suggestions">
              {[
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
              ].map((item) => (
                <button
                  key={item.icon}
                  disabled={busy || status !== "connected"}
                  onClick={() => void sendMessage(item.text)}
                >
                  <span className="suggestion-number">{item.icon}</span>
                  <span>
                    <strong>{item.label}</strong>
                    <small>{item.detail}</small>
                  </span>
                  <ArrowUp size={15} className="diagonal-arrow" />
                </button>
              ))}
            </div>
            <p className="welcome-foot">
              <CheckCheck size={14} /> Her adımın gerekçesi görünür.
            </p>
          </div>
        )}
        {requests.map((request) => (
          <article className="exchange" key={request.id}>
            <div className="user-message">
              <span className="message-label">
                SEN {request.source === "voice" && <Mic size={11} />}
              </span>
              <p>{request.text}</p>
            </div>
            <div className="assistant-message">
              <div className="assistant-label">
                <PolarisPet
                  size={25}
                  mood={request.status === "running" ? "thinking" : "idle"}
                  animated={request.status === "running"}
                />{" "}
                POLARIS
                {request.status === "running" && (
                  <LoaderCircle className="spin" size={13} />
                )}
              </div>
              {request.answer && <Markdown text={request.answer} />}
              {request.status === "running" && !request.answer && (
                <p className="working">
                  {request.steps.at(-1)?.decision ?? "İsteğini alıyorum…"}
                </p>
              )}
              {request.status === "error" && (
                <p className="error" role="alert">
                  {request.error}
                </p>
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
              {request.spoken && (
                <button
                  className="text-button listen"
                  onClick={() =>
                    void speaker.speak(request.spoken, settings.mock)
                  }
                >
                  <Volume2 size={13} /> Sesli oku
                </button>
              )}
            </div>
          </article>
        ))}
        <div ref={scrollEnd} />
      </section>
      <footer className="composer-area">
        {error && (
          <div className="error error-banner" role="alert">
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
              void grantPermission().catch(() =>
                reportError("İzin sekmesi açılamadı."),
              )
            }
          >
            Mikrofon izni ver
          </button>
        )}
        {activeId && (
          <button className="cancel-task" onClick={cancel}>
            <CircleStop size={14} /> Görevi iptal et
          </button>
        )}
        {speaker.playing && (
          <button className="text-button" onClick={speaker.stop}>
            <CircleStop size={14} /> Sesli okumayı durdur
          </button>
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
              {
                {
                  idle: "Bas-konuş · Space",
                  starting: "Mikrofon açılıyor…",
                  recording: "Dinliyorum… bırakınca gönder",
                  transcribing: "Ses yazıya çevriliyor…",
                }[recorder.state]
              }
            </span>
            <button
              className="send-button"
              type="submit"
              disabled={!draft.trim() || busy || status !== "connected"}
              aria-label="Mesajı gönder"
            >
              <ArrowUp size={19} />
            </button>
          </div>
        </form>
        <p className="footer-note">
          <AudioLines size={11} />{" "}
          {settings.mock
            ? "Mock gösterimi · Gerçek işlem yapılmaz"
            : "Önemli işlemler için onayın gerekir."}
        </p>
      </footer>
      {showSettings && (
        <SettingsModal
          current={settings}
          busy={busy}
          onClose={() => setShowSettings(false)}
          onSave={async (next) => {
            const normalized = {
              ...next,
              backend: normalizeBackend(next.backend),
            };
            if (
              busyRef.current &&
              (normalized.backend !== settings.backend ||
                normalized.mock !== settings.mock)
            )
              throw new Error("Önce mevcut görevi bitir veya iptal et.");
            await saveSettings(normalized);
            speakerRef.current.stop();
            setSettings(normalized);
            setError("");
          }}
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
