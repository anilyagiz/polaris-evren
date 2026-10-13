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
    let pending: Promise<void> = Promise.resolve();
    await act(async () => {
      pending = result.current.speak("Merhaba");
      await vi.waitFor(() => expect(speak).toHaveBeenCalled());
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/tts");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ text: "Merhaba" });
    expect(speak.mock.calls[0][0].lang).toBe("tr-TR");
    act(() => result.current.stop());
    await act(async () => {
      await pending;
    });
    expect(result.current.playing).toBe(false);
  });

  function mockMic() {
    const stopTrack = vi.fn();
    const media = {
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => media },
    });
    return stopTrack;
  }
  it("ignores recordings shorter than 400ms", async () => {
    const stopTrack = mockMic();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", vi.fn(), vi.fn()),
    );
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(100);
      result.current.stop();
      await Promise.resolve();
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(result.current.state).toBe("idle");
  });
  it("auto-stops after 30 seconds", async () => {
    mockMic();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: "x" }) }),
    );
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", vi.fn(), vi.fn()),
    );
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(30001);
      await Promise.resolve();
    });
    expect(result.current.state).not.toBe("recording");
  });
  it("opens the permission tab on NotFoundError", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("none", "NotFoundError");
        },
      },
    });
    const create = vi.fn().mockResolvedValue({});
    vi.stubGlobal("chrome", {
      tabs: { create },
      runtime: { getURL: (path: string) => `chrome-extension://x/${path}` },
    });
    const { result } = renderHook(() =>
      useRecorder("http://localhost:8000", vi.fn(), vi.fn()),
    );
    await act(async () => {
      await result.current.start();
    });
    expect(create).toHaveBeenCalledWith({
      url: "chrome-extension://x/src/panel/permission.html",
    });
    expect(result.current.permissionNeeded).toBe(true);
  });
  it("queues TTS replies without overlapping and reports autoplay blocks", async () => {
    const players: { play: ReturnType<typeof vi.fn>; end: () => void }[] = [];
    class FakeAudio {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      play = vi.fn().mockResolvedValue(undefined);
      pause = vi.fn();
      constructor(_url: string) {
        players.push({ play: this.play, end: () => this.onended?.() });
      }
    }
    vi.stubGlobal("Audio", FakeAudio);
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("speechSynthesis", { speak: vi.fn(), cancel: vi.fn(), getVoices: () => [] });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["mp3"]),
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useSpeaker("http://localhost:8000", vi.fn()),
    );
    await act(async () => {
      void result.current.speak("bir");
      void result.current.speak("iki");
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(players).toHaveLength(1);
    expect(result.current.playing).toBe(true);
    await act(async () => {
      players[0].end();
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(players).toHaveLength(2);
    await act(async () => {
      players[1].end();
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.playing).toBe(false);
  });
  it("waits for a gesture when autoplay is rejected", async () => {
    const play = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"))
      .mockResolvedValue(undefined);
    let ended: (() => void) | null = null;
    class FakeAudio {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      play = play;
      pause = vi.fn();
      constructor(_url: string) {
        ended = () => this.onended?.();
      }
    }
    vi.stubGlobal("Audio", FakeAudio);
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["m"]) }));
    const onError = vi.fn();
    const { result } = renderHook(() => useSpeaker("http://localhost:8000", onError));
    await act(async () => {
      void result.current.speak("merhaba");
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.blocked).toBe(true);
    expect(onError).toHaveBeenCalled();
    await act(async () => {
      result.current.resume();
      await vi.advanceTimersByTimeAsync(10);
      ended?.();
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(play).toHaveBeenCalledTimes(2);
    expect(result.current.blocked).toBe(false);
    expect(result.current.playing).toBe(false);
  });
});
