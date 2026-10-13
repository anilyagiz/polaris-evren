/**
 * Polaris desktop mascot: the PolarisPet star in a small frameless, always-on-top
 * pywebview window (tray/mascot.py) served by the backend at
 * /app/src/panel/mascot.html. It speaks the same WebSocket protocol as the panel
 * (hello client "desktop", tab null) and shows its mood live.
 *
 * Click: speech bubble with a task input + push-to-talk.  Double click: the full
 * Polaris window (pywebview js_api open_main).  Reminders shake the star and
 * appear in the bubble.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { PolarisPet } from "./PolarisPet";
import {
  requestId,
  type Confirmation,
  type ConnectionStatus,
  type ServerEvent,
} from "./protocol";
import { refuseBrowserTool } from "./runtime";
import { defaultSettings, loadSettings, type Settings } from "./settings";
import { useRecorder, useSpeaker } from "./voice";
import { connectSocket, type Transport } from "./ws";
import {
  BUBBLE_AUTO_HIDE_MS,
  CLICK_DELAY_MS,
  DRAG_SLOP_PX,
  hitRegions,
  hostCall,
  mascotMood,
} from "./mascotState";
import "./mascot.css";

type Bubble =
  | { kind: "closed" }
  | { kind: "input" }
  | { kind: "working"; text: string }
  | { kind: "answer"; text: string }
  | { kind: "notify"; text: string }
  | { kind: "error"; text: string }
  | { kind: "confirm"; confirm: Confirmation };

const AUTO_HIDE = new Set(["answer", "notify", "error"]);

export function Mascot() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [bubble, setBubble] = useState<Bubble>({ kind: "closed" });
  const [draft, setDraft] = useState("");
  const [focused, setFocused] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const [hovered, setHovered] = useState(false);
  const transport = useRef<Transport | null>(null);
  const busyRef = useRef<string | null>(null);
  busyRef.current = busyId;
  const clickTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const press = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const backend = settings?.backend ?? defaultSettings.backend;

  const showError = useCallback(
    (text: string) => setBubble({ kind: "error", text }),
    [],
  );
  const speaker = useSpeaker(backend, () => undefined);
  const speakerRef = useRef(speaker);
  speakerRef.current = speaker;

  /** "Sesli yanıt" can change in the main window (same origin storage). */
  const speakIfEnabled = useCallback(async (text: string) => {
    if (!text.trim()) return;
    const current = await loadSettings();
    if (current.speak) speakerRef.current.speak(text);
  }, []);

  const send = useCallback(
    (text: string, source: "text" | "voice") => {
      const clean = text.trim();
      if (!clean) return;
      const id = requestId();
      const ok =
        transport.current?.send({
          type: "user_msg",
          id,
          text: clean,
          source,
          tab: null,
        }) ?? false;
      if (!ok) {
        showError("Polaris'e bağlanılamadı. Sunucu çalışıyor mu?");
        return;
      }
      speakerRef.current.stop();
      setDraft("");
      setBusyId(id);
      setBubble({ kind: "working", text: clean });
    },
    [showError],
  );

  const recorder = useRecorder(
    backend,
    (text) => send(text, "voice"),
    showError,
  );

  const onEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case "final":
          if (busyRef.current && event.request_id !== busyRef.current) return;
          setBusyId(null);
          setBubble({ kind: "answer", text: event.answer });
          void speakIfEnabled(event.spoken || event.answer);
          return;
        case "need_confirm":
          setBubble({ kind: "confirm", confirm: event });
          return;
        case "tool_call":
          // The mascot has no browser behind it: refuse like the desktop panel.
          transport.current?.send({
            type: "tool_result",
            call_id: event.call_id,
            ...refuseBrowserTool(),
          });
          return;
        case "notify":
          setShake(true);
          setBubble({ kind: "notify", text: event.text });
          void speakIfEnabled(event.spoken || event.text);
          return;
        case "error":
          if (event.request_id && event.request_id === busyRef.current)
            setBusyId(null);
          if (!event.request_id || event.request_id === busyRef.current)
            setBubble({ kind: "error", text: event.message });
          return;
        default:
          return;
      }
    },
    [speakIfEnabled],
  );

  useEffect(() => {
    let cancelled = false;
    void loadSettings().then((loaded) => {
      if (!cancelled) setSettings(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!settings) return;
    const socket = connectSocket(settings.backend, onEvent, setStatus, showError);
    transport.current = socket;
    return () => {
      socket.close();
      transport.current = null;
    };
  }, [settings, onEvent, showError]);

  // Window size follows the bubble (bottom-right corner stays put).
  const open = bubble.kind !== "closed";
  useEffect(() => {
    hostCall("set_expanded", open);
  }, [open]);

  // Tell the host which pixels are clickable (colour-keyed windows are
  // click-through elsewhere). Re-sent on layout changes and every 1.5 s.
  const lastHit = useRef("");
  useEffect(() => {
    const report = () => {
      const pet = document.querySelector(".mascot-pet");
      const bub = document.querySelector(".mascot-bubble");
      const regions = hitRegions(
        pet ? pet.getBoundingClientRect() : null,
        bub ? bub.getBoundingClientRect() : null,
      );
      const key = JSON.stringify([regions, window.innerWidth, window.innerHeight]);
      if (key === lastHit.current) return;
      if (hostCall("set_hit_regions", regions)) lastHit.current = key;
    };
    const frame = requestAnimationFrame(report);
    let ticks = 0;
    const timer = setInterval(() => {
      // Re-send now and then even unchanged: the host may have missed one.
      if (++ticks % 4 === 0) lastHit.current = "";
      report();
    }, 1500);
    window.addEventListener("resize", report);
    window.addEventListener("pywebviewready", report);
    return () => {
      cancelAnimationFrame(frame);
      clearInterval(timer);
      window.removeEventListener("resize", report);
      window.removeEventListener("pywebviewready", report);
    };
  }, [bubble, status]);

  useEffect(() => {
    if (!shake) return;
    const timer = setTimeout(() => setShake(false), 1300);
    return () => clearTimeout(timer);
  }, [shake]);

  useEffect(() => {
    if (!AUTO_HIDE.has(bubble.kind) || hovered) return;
    const timer = setTimeout(
      () => setBubble({ kind: "closed" }),
      BUBBLE_AUTO_HIDE_MS,
    );
    return () => clearTimeout(timer);
  }, [bubble, hovered]);

  useEffect(() => () => clearTimeout(clickTimer.current), []);

  const answerConfirm = (approved: boolean) => {
    if (bubble.kind !== "confirm") return;
    transport.current?.send({
      type: "confirm",
      call_id: bubble.confirm.call_id,
      approved,
    });
    setBubble(
      busyRef.current
        ? { kind: "working", text: approved ? "Onaylandı" : "Reddedildi" }
        : { kind: "closed" },
    );
  };

  // Double clicks are detected by hand: pywebview's drag region repositions the
  // window on mousedown, which makes WebView2 drop the native dblclick.
  const lastClick = useRef(0);
  const onPetClick = () => {
    if (press.current?.moved) return;
    clearTimeout(clickTimer.current);
    const now = Date.now();
    if (now - lastClick.current < CLICK_DELAY_MS + 140) {
      lastClick.current = 0;
      onPetDoubleClick();
      return;
    }
    lastClick.current = now;
    clickTimer.current = setTimeout(() => {
      speakerRef.current.resume();
      setBubble((current) =>
        current.kind === "closed"
          ? { kind: "input" }
          : current.kind === "confirm" || current.kind === "working"
            ? current
            : { kind: "closed" },
      );
    }, CLICK_DELAY_MS);
  };
  function onPetDoubleClick() {
    clearTimeout(clickTimer.current);
    hostCall("open_main");
  }

  const mood = mascotMood({
    confirming: bubble.kind === "confirm",
    recording:
      recorder.state === "recording" || recorder.state === "transcribing",
    typing: focused && draft.trim().length > 0,
    busy: busyId !== null,
    speaking: speaker.playing,
  });
  const recording = recorder.state !== "idle";

  return (
    <main
      className={`mascot${open ? " mascot-open" : ""}`}
      data-mood={mood}
      data-status={status}
    >
      {open && (
        <section
          className={`mascot-bubble mascot-bubble-${bubble.kind}`}
          aria-live="polite"
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
        >
          <button
            type="button"
            className="mascot-close"
            aria-label="Balonu kapat"
            onClick={() => setBubble({ kind: "closed" })}
          >
            ×
          </button>
          {bubble.kind === "working" && (
            <p className="mascot-working">
              <span className="mascot-dots" aria-hidden="true" />
              Üzerinde çalışıyorum…
              <small>{bubble.text}</small>
            </p>
          )}
          {(bubble.kind === "answer" ||
            bubble.kind === "notify" ||
            bubble.kind === "error") && (
            <div className="mascot-text" role={bubble.kind === "error" ? "alert" : undefined}>
              {bubble.kind === "notify" && (
                <strong className="mascot-label">Hatırlatma</strong>
              )}
              {bubble.text}
            </div>
          )}
          {bubble.kind === "confirm" && (
            <div className="mascot-confirm">
              <strong className="mascot-label">Onayın gerekiyor</strong>
              <p>{bubble.confirm.summary}</p>
              <div className="mascot-actions">
                <button
                  type="button"
                  className="mascot-primary"
                  onClick={() => answerConfirm(true)}
                >
                  Onayla
                </button>
                <button type="button" onClick={() => answerConfirm(false)}>
                  Reddet
                </button>
              </div>
            </div>
          )}
          {(bubble.kind === "input" ||
            bubble.kind === "answer" ||
            bubble.kind === "error" ||
            bubble.kind === "notify") && (
            <form
              className="mascot-form"
              onSubmit={(event) => {
                event.preventDefault();
                send(draft, "text");
              }}
            >
              <input
                aria-label="Polaris'e görev"
                placeholder={
                  recording
                    ? recorder.state === "transcribing"
                      ? "Yazıya çeviriyorum…"
                      : `Dinliyorum… ${recorder.elapsed}s`
                    : "Polaris'e bir görev ver…"
                }
                value={draft}
                autoFocus={bubble.kind === "input"}
                onChange={(event) => setDraft(event.target.value)}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setBubble({ kind: "closed" });
                }}
              />
              <button
                type="button"
                className={`mascot-mic${recording ? " is-recording" : ""}`}
                aria-label="Basılı tut ve konuş"
                title="Basılı tut ve konuş"
                onPointerDown={() => void recorder.start()}
                onPointerUp={recorder.stop}
                onPointerLeave={recorder.stop}
              >
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"
                  />
                </svg>
              </button>
              <button
                type="submit"
                className="mascot-send"
                aria-label="Görevi gönder"
                disabled={!draft.trim()}
              >
                ➤
              </button>
            </form>
          )}
          {bubble.kind === "input" && (
            <div className="mascot-footer">
              <button type="button" onClick={() => hostCall("open_main")}>
                Polaris penceresi
              </button>
              <button type="button" onClick={() => hostCall("hide_mascot")}>
                Maskotu gizle
              </button>
            </div>
          )}
          {status !== "connected" && (
            <p className="mascot-status">
              {status === "connecting" ? "Bağlanıyor…" : "Sunucu bağlantısı yok"}
            </p>
          )}
        </section>
      )}
      <div
        className={`mascot-pet pywebview-drag-region${shake ? " mascot-shake" : ""}`}
        title="Tıkla: görev ver · Çift tıkla: Polaris penceresi · Sürükle: taşı"
        onPointerDown={(event) => {
          press.current = { x: event.screenX, y: event.screenY, moved: false };
        }}
        onPointerMove={(event) => {
          const start = press.current;
          if (
            start &&
            !start.moved &&
            Math.hypot(event.screenX - start.x, event.screenY - start.y) >
              DRAG_SLOP_PX
          )
            start.moved = true;
        }}
        onClickCapture={onPetClick}
        onContextMenu={(event) => {
          event.preventDefault();
        }}
      >
        <PolarisPet mood={mood} size={120} compact />
      </div>
    </main>
  );
}

const root = document.getElementById("mascot-root");
if (root) createRoot(root).render(<Mascot />);
