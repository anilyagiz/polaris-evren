import { useCallback, useEffect, useRef, useState } from "react";

export function useSpeaker(
  backend: string,
  onError: (message: string) => void,
) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const objectUrl = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const [playing, setPlaying] = useState(false);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    audio.current?.pause();
    audio.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
    window.speechSynthesis?.cancel();
    setPlaying(false);
  }, []);
  const speak = useCallback(
    async (text: string, mock = false) => {
      if (!text.trim()) return;
      stop();
      const controller = new AbortController();
      abort.current = controller;
      setPlaying(true);
      let fallbackStarted = false;
      const fallback = () => {
        if (controller.signal.aborted || fallbackStarted) return;
        fallbackStarted = true;
        if (!window.speechSynthesis) {
          setPlaying(false);
          errorRef.current("Bu tarayıcı sesli okumayı desteklemiyor.");
          return;
        }
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "tr-TR";
        utterance.voice =
          window.speechSynthesis
            .getVoices()
            .find((voice) => voice.lang.startsWith("tr")) ?? null;
        utterance.onend = () => {
          if (!controller.signal.aborted) setPlaying(false);
        };
        utterance.onerror = () => {
          if (!controller.signal.aborted) {
            setPlaying(false);
            errorRef.current(
              "Ses oynatılamadı. Cevabı ekrandan okuyabilirsin.",
            );
          }
        };
        window.speechSynthesis.speak(utterance);
      };
      try {
        if (mock) {
          fallback();
          return;
        }
        const response = await fetch(`${backend}/tts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("TTS failed");
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl.current = URL.createObjectURL(blob);
        const player = new Audio(objectUrl.current);
        audio.current = player;
        player.onended = () => {
          if (audio.current === player) stop();
        };
        player.onerror = () => {
          if (controller.signal.aborted || audio.current !== player) return;
          if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
          objectUrl.current = null;
          audio.current = null;
          fallback();
        };
        await player.play();
      } catch {
        if (controller.signal.aborted) return;
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
        objectUrl.current = null;
        audio.current = null;
        fallback();
      }
    },
    [backend, stop],
  );
  useEffect(() => stop, [stop]);
  return { speak, stop, playing };
}

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
  const [state, setState] = useState<
    "idle" | "starting" | "recording" | "transcribing"
  >("idle");
  const [permissionNeeded, setPermissionNeeded] = useState(false);
  const textRef = useRef(onText);
  textRef.current = onText;
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const stop = useCallback(() => {
    held.current = false;
    clearTimeout(limit.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);
  const start = useCallback(async () => {
    if (held.current || acquiring.current || state !== "idle") return;
    held.current = true;
    acquiring.current = true;
    setState("starting");
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
      const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      if (!mimeType)
        throw new Error("Tarayıcı WebM ses kaydını desteklemiyor.");
      const capture = new MediaRecorder(media, { mimeType });
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
        clearTimeout(limit.current);
        media.getTracks().forEach((track) => track.stop());
        stream.current = null;
        recorder.current = null;
        if (!active.current) return;
        if (Date.now() - started < 250 || !chunks.length) {
          setState("idle");
          return;
        }
        setState("transcribing");
        const controller = new AbortController();
        abort.current = controller;
        const form = new FormData();
        form.append(
          "audio",
          new Blob(chunks, { type: mimeType }),
          "speech.webm",
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
      limit.current = setTimeout(stop, 60000);
    } catch (error) {
      held.current = false;
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      if (!active.current) return;
      setState("idle");
      const denied =
        error instanceof DOMException &&
        ["NotAllowedError", "SecurityError"].includes(error.name);
      setPermissionNeeded(denied);
      errorRef.current(
        denied
          ? "Mikrofon izni gerekiyor. “Mikrofon izni ver” düğmesini kullan."
          : error instanceof Error
            ? error.message
            : "Mikrofon açılamadı.",
      );
    } finally {
      acquiring.current = false;
    }
  }, [backend, state, stop]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      held.current = false;
      clearTimeout(limit.current);
      abort.current?.abort();
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);
  return { start, stop, state, permissionNeeded };
}
