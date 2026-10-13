/**
 * Polaris voice layer (push-to-talk STT + queued TTS playback).
 *
 * useRecorder(backend, onText, onError)
 *   -> { start, stop, state, elapsed, permissionNeeded, openPermissionPage }
 *   - start(): call on pointerdown / Space keydown (user gesture). Records WebM/Opus.
 *   - stop():  call on release. Recordings < 400 ms are discarded; auto-stops at 30 s.
 *   - state: "idle" | "starting" | "recording" | "transcribing"; elapsed: whole
 *     seconds while recording.
 *   - On NotAllowedError/NotFoundError the permission page is opened in a new tab
 *     and permissionNeeded=true; when the page stores a grant
 *     (chrome.storage.local.micPermission = "granted") permissionNeeded clears
 *     and the user simply holds the button again.
 *   - POSTs multipart field `audio` to `${backend}/stt`, expects {text}.
 *
 * useSpeaker(backend, onError) -> { speak(text, mock?), stop(), resume(), playing, blocked }
 *   - speak() enqueues; replies never overlap. stop() clears the queue and halts audio.
 *   - POST `${backend}/tts` {text} -> mp3 -> Audio; on failure (or mock) falls back
 *     to speechSynthesis (tr-TR, Turkish voice preferred).
 *   - If the browser rejects autoplay, blocked=true and the item waits for
 *     resume() (call it from a click).
 *   - Callers gate on the "Sesli yanıt" setting before calling speak().
 */
import { useCallback, useEffect, useRef, useState } from "react";

export const MIN_RECORDING_MS = 400;
export const MAX_RECORDING_MS = 30000;
const PERMISSION_PATH = "src/panel/permission.html";

export function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ].find((type) => MediaRecorder.isTypeSupported(type));
}

export async function openPermissionPage(): Promise<void> {
  if (
    typeof chrome !== "undefined" &&
    chrome.tabs?.create &&
    chrome.runtime?.getURL
  ) {
    await chrome.tabs.create({ url: chrome.runtime.getURL(PERMISSION_PATH) });
  } else {
    window.open("./permission.html", "_blank", "noopener");
  }
}

export function describeMicError(error: unknown): {
  message: string;
  needsPermission: boolean;
} {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return {
      needsPermission: true,
      message:
        "Mikrofon izni verilmedi. Açılan sekmede izin ver; Brave Shields veya Windows gizlilik ayarları mikrofonu engelliyor olabilir.",
    };
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return {
      needsPermission: true,
      message:
        "Mikrofon bulunamadı veya izin henüz verilmedi. Mikrofonun bağlı olduğunu kontrol edip açılan sekmeden izin ver.",
    };
  if (name === "NotReadableError" || name === "AbortError")
    return {
      needsPermission: false,
      message:
        "Mikrofon başka bir uygulama tarafından kullanılıyor ya da Windows tarafından engellendi (Ayarlar > Gizlilik > Mikrofon).",
    };
  return {
    needsPermission: false,
    message: error instanceof Error ? error.message : "Mikrofon açılamadı.",
  };
}

function pickTurkishVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis?.getVoices?.() ?? [];
  return (
    voices.find((voice) => voice.lang.toLowerCase().startsWith("tr")) ?? null
  );
}

type QueueItem = { text: string; mock: boolean };

export function useSpeaker(
  backend: string,
  onError: (message: string) => void,
) {
  const queue = useRef<QueueItem[]>([]);
  const running = useRef(false);
  const generation = useRef(0);
  const audio = useRef<HTMLAudioElement | null>(null);
  const objectUrl = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const finish = useRef<(() => void) | null>(null);
  const unblock = useRef<(() => void) | null>(null);
  const mounted = useRef(true);
  const [playing, setPlaying] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const backendRef = useRef(backend);
  backendRef.current = backend;

  const release = () => {
    audio.current?.pause();
    audio.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
  };

  const stop = useCallback(() => {
    generation.current += 1;
    queue.current = [];
    abort.current?.abort();
    abort.current = null;
    release();
    window.speechSynthesis?.cancel();
    finish.current?.();
    finish.current = null;
    unblock.current?.();
    unblock.current = null;
    if (mounted.current) {
      setPlaying(false);
      setBlocked(false);
    }
  }, []);

  const speakWithBrowserVoice = (text: string, gen: number) =>
    new Promise<void>((resolve) => {
      if (
        !window.speechSynthesis ||
        typeof SpeechSynthesisUtterance === "undefined"
      ) {
        errorRef.current("Bu tarayıcı sesli okumayı desteklemiyor.");
        resolve();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "tr-TR";
      utterance.voice = pickTurkishVoice();
      finish.current = resolve;
      utterance.onend = () => resolve();
      utterance.onerror = () => {
        if (gen === generation.current)
          errorRef.current("Ses oynatılamadı. Cevabı ekrandan okuyabilirsin.");
        resolve();
      };
      window.speechSynthesis.speak(utterance);
    });

  const playItem = async (item: QueueItem, gen: number) => {
    if (item.mock) {
      await speakWithBrowserVoice(item.text, gen);
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    let blob: Blob;
    try {
      const response = await fetch(`${backendRef.current}/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("TTS failed");
      blob = await response.blob();
    } catch {
      if (gen !== generation.current) return;
      await speakWithBrowserVoice(item.text, gen);
      return;
    }
    if (gen !== generation.current) return;
    const url = URL.createObjectURL(blob);
    objectUrl.current = url;
    const player = new Audio(url);
    audio.current = player;
    const ended = new Promise<"ended" | "error">((resolve) => {
      finish.current = () => resolve("ended");
      player.onended = () => resolve("ended");
      player.onerror = () => resolve("error");
    });
    let started = false;
    try {
      await player.play();
      started = true;
    } catch (error) {
      if (gen !== generation.current) return;
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        // Autoplay blocked: wait for a user gesture calling resume().
        if (mounted.current) setBlocked(true);
        errorRef.current(
          "Tarayıcı otomatik sesi engelledi. “Sesli okumayı başlat” düğmesine bas.",
        );
        await new Promise<void>((resolve) => {
          unblock.current = resolve;
        });
        unblock.current = null;
        if (gen !== generation.current) return;
        try {
          await player.play();
          started = true;
        } catch {
          started = false;
        }
      }
    }
    if (gen !== generation.current) return;
    const result = started ? await ended : "error";
    release();
    if (gen !== generation.current) return;
    if (result === "error") await speakWithBrowserVoice(item.text, gen);
  };

  const pump = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    const gen = generation.current;
    if (mounted.current) setPlaying(true);
    try {
      while (queue.current.length && gen === generation.current) {
        const item = queue.current.shift()!;
        await playItem(item, gen);
        finish.current = null;
      }
    } finally {
      running.current = false;
      if (gen === generation.current && mounted.current) setPlaying(false);
      // stop() bumped the generation; items queued afterwards still need playing.
      if (gen !== generation.current && queue.current.length) void pump();
    }
    // playItem only touches refs, so it is stable for our purposes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const speak = useCallback(
    async (text: string, mock = false) => {
      if (!text.trim()) return;
      queue.current.push({ text, mock });
      await pump();
    },
    [pump],
  );

  const resume = useCallback(() => {
    if (mounted.current) setBlocked(false);
    unblock.current?.();
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stop();
    };
  }, [stop]);
  return { speak, stop, resume, playing, blocked };
}

type RecorderState = "idle" | "starting" | "recording" | "transcribing";

export function useRecorder(
  backend: string,
  onText: (text: string) => void,
  onError: (message: string) => void,
) {
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const held = useRef(false);
  const acquiring = useRef(false);
  const active = useRef(true);
  const abort = useRef<AbortController | null>(null);
  const limit = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const ticker = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const [state, setState] = useState<RecorderState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [permissionNeeded, setPermissionNeeded] = useState(false);
  const textRef = useRef(onText);
  textRef.current = onText;
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const stateRef = useRef<RecorderState>("idle");
  stateRef.current = state;

  const clearTimers = () => {
    clearTimeout(limit.current);
    clearInterval(ticker.current);
    ticker.current = undefined;
  };
  const stop = useCallback(() => {
    held.current = false;
    clearTimers();
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (held.current || acquiring.current || stateRef.current !== "idle")
      return;
    held.current = true;
    acquiring.current = true;
    setState("starting");
    setElapsed(0);
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === "undefined"
      )
        throw new Error("Bu tarayıcıda mikrofon kaydı kullanılamıyor.");
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!held.current || !active.current) {
        media.getTracks().forEach((track) => track.stop());
        if (active.current) setState("idle");
        return;
      }
      stream.current = media;
      const mimeType = pickMimeType();
      const capture = mimeType
        ? new MediaRecorder(media, { mimeType })
        : new MediaRecorder(media);
      const blobType = mimeType ?? (capture.mimeType || "audio/webm");
      recorder.current = capture;
      const chunks: Blob[] = [];
      const started = Date.now();
      capture.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      capture.onerror = () => {
        errorRef.current("Ses kaydı başarısız oldu.");
        stop();
      };
      capture.onstop = async () => {
        clearTimers();
        media.getTracks().forEach((track) => track.stop());
        stream.current = null;
        recorder.current = null;
        held.current = false;
        if (!active.current) return;
        if (Date.now() - started < MIN_RECORDING_MS || !chunks.length) {
          setState("idle");
          setElapsed(0);
          return;
        }
        setState("transcribing");
        setElapsed(0);
        const controller = new AbortController();
        abort.current = controller;
        const form = new FormData();
        const ext = blobType.includes("webm")
          ? "webm"
          : blobType.includes("ogg")
            ? "ogg"
            : "m4a";
        form.append(
          "audio",
          new Blob(chunks, { type: blobType }),
          `speech.${ext}`,
        );
        try {
          const response = await fetch(`${backend}/stt`, {
            method: "POST",
            body: form,
            signal: controller.signal,
          });
          if (!response.ok)
            throw new Error(
              "Ses yazıya çevrilemedi. Backend /stt servisini kontrol et.",
            );
          const result: unknown = await response.json();
          if (
            typeof result !== "object" ||
            result === null ||
            !("text" in result) ||
            typeof result.text !== "string"
          )
            throw new Error("STT yanıtı geçersiz.");
          if (!result.text.trim())
            throw new Error("Ses algılanmadı. Tekrar konuşmayı dene.");
          if (active.current && !controller.signal.aborted)
            textRef.current(result.text.trim());
        } catch (error) {
          if (!controller.signal.aborted)
            errorRef.current(
              error instanceof Error
                ? error.message
                : "Ses bağlantısı başarısız.",
            );
        } finally {
          if (active.current) setState("idle");
        }
      };
      setPermissionNeeded(false);
      capture.start();
      setState("recording");
      ticker.current = setInterval(
        () => setElapsed(Math.floor((Date.now() - started) / 1000)),
        250,
      );
      limit.current = setTimeout(stop, MAX_RECORDING_MS);
    } catch (error) {
      held.current = false;
      clearTimers();
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      if (!active.current) return;
      setState("idle");
      const { message, needsPermission } = describeMicError(error);
      setPermissionNeeded(needsPermission);
      errorRef.current(message);
      if (needsPermission) void openPermissionPage().catch(() => undefined);
    } finally {
      acquiring.current = false;
    }
  }, [backend, stop]);

  // The permission page writes chrome.storage.local.micPermission = "granted".
  useEffect(() => {
    if (typeof chrome === "undefined" || !chrome.storage?.onChanged) return;
    const listener = (changes: Record<string, chrome.storage.StorageChange>) => {
      if (changes.micPermission?.newValue === "granted")
        setPermissionNeeded(false);
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }, []);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      held.current = false;
      clearTimers();
      abort.current?.abort();
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);
  return {
    start,
    stop,
    state,
    elapsed,
    permissionNeeded,
    openPermissionPage,
  };
}
