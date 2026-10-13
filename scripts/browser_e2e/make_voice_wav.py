"""Convert an mp3 (from the backend's POST /tts) to a 16 kHz mono s16 WAV for
Chromium's --use-file-for-fake-audio-capture.

    python make_voice_wav.py in.mp3 out.wav [lead_s] [tail_s]

Silence is padded before/after the speech so a ~5 s push-to-talk hold catches
the whole sentence once. Prints {"seconds": <total>} as JSON.
"""
from __future__ import annotations

import json
import sys
import wave

import av
import numpy as np

RATE = 16_000


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    lead = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
    tail = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
    container = av.open(src)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks: list[np.ndarray] = []
    for frame in container.decode(audio=0):
        for out in resampler.resample(frame):
            chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    container.close()
    speech = np.concatenate(chunks).astype(np.int16)
    pcm = np.concatenate([
        np.zeros(int(lead * RATE), np.int16), speech, np.zeros(int(tail * RATE), np.int16),
    ])
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    print(json.dumps({"seconds": round(len(pcm) / RATE, 2), "speech_s": round(len(speech) / RATE, 2)}))


if __name__ == "__main__":
    main()
