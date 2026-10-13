// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRecorder, useSpeaker } from "./voice";

class FakeRecorder {
  static isTypeSupported(type: string) {
    return type.startsWith("audio/webm");
  }
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => Promise<void>) | null = null;
  onerror: (() => void) | null = null;
  constructor(_stream: MediaStream, _options: MediaRecorderOptions) {}
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({
      data: new Blob(["voice"], { type: "audio/webm;codecs=opus" }),
    });
    void this.onstop?.();
  }
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("MediaRecorder", FakeRecorder);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Voice boundaries", () => {
  it("posts WebM using the audio multipart field and forwards STT text", async () => {
    const stopTrack = vi.fn();
    const media = {
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream;
    const fetchMock = vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          text: "Bu sayfayı özetle",
          language: "tr",
          duration_s: 1,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => media },
    });
    const onText = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", onText, onError),
    );
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.state).toBe("recording");
    await act(async () => {
      vi.advanceTimersByTime(600);
      result.current.stop();
      await Promise.resolve();
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/stt");
    const options = fetchMock.mock.calls[0][1] as RequestInit;
    expect((options.body as FormData).get("audio")).toBeTruthy();
    expect(onText).toHaveBeenCalledWith("Bu sayfayı özetle");
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
    expect(result.current.state).toBe("idle");
  });
  it("releases media if the user lets go before permission resolves", async () => {
    const stopTrack = vi.fn();
    const media = {
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream;
    let resolveMedia!: (stream: MediaStream) => void;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: () =>
          new Promise<MediaStream>((resolve) => {
            resolveMedia = resolve;
          }),
      },
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", vi.fn(), vi.fn()),
    );
    act(() => {
      void result.current.start();
      result.current.stop();
    });
    await act(async () => {
      resolveMedia(media);
      await Promise.resolve();
    });
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.state).toBe("idle");
  });
  it("shows the permission-page route after a microphone denial", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("Denied", "NotAllowedError");
        },
      },
    });
    const onError = vi.fn();
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", vi.fn(), onError),
    );
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.permissionNeeded).toBe(true);
    expect(onError).toHaveBeenCalled();
  });
  it("falls back to Turkish speech synthesis if the TTS endpoint fails", async () => {
    const speak = vi.fn();
    const cancel = vi.fn();
    vi.stubGlobal("speechSynthesis", { speak, cancel, getVoices: () => [] });
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        lang = "";
        voice = null;
        onend = null;
        onerror = null;
        constructor(public text: string) {}
      },
    );
    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useSpeaker("http://localhost:8000", vi.fn()),
    );
    await act(async () => {
      await result.current.speak("Merhaba");
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/tts");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ text: "Merhaba" });
    expect(speak.mock.calls[0][0].lang).toBe("tr-TR");
    act(() => result.current.stop());
    expect(result.current.playing).toBe(false);
  });
});
