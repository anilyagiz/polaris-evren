#!/usr/bin/env python3
"""Person 5 / K5 — voice smoke test (STT + TTS).

Transcribes a sample recording to text and renders a Turkish sentence to mp3.
Runs standalone: it builds the FastAPI app in memory and drives it with TestClient.

Usage:
    python scripts/test_voice.py                 # generates the sample itself (edge-tts)
    python scripts/test_voice.py path/to/audio.webm   # use your own file

Note: the first run downloads the faster-whisper model (~460 MB for "small").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The Windows console may be cp1252; keep Turkish output from crashing.
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("_", "-") != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("chcp 65001 >NUL")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db import load_env  # noqa: E402
from backend.voice.stt import router as stt_router  # noqa: E402
from backend.voice.tts import router as tts_router  # noqa: E402

SAMPLE_SENTENCE = "Merhaba, bu bir ses testi. Kişi beşin ses modülü çalışıyor."
WORK_DIR = ROOT / "backend" / "data" / "test_voice"


# --- sample generation -----------------------------------------------------

def _synth_sample_via_api(client: TestClient, dst: Path) -> bool:
    """Produce the sample through the real /tts endpoint (same path as the demo)."""
    resp = client.post("/tts", json={"text": SAMPLE_SENTENCE})
    if resp.status_code == 200 and len(resp.content) > 1000:
        dst.write_bytes(resp.content)
        print(f"  sample mp3 produced via /tts ({len(resp.content)} bytes)")
        return True
    print(f"  [!] /tts could not produce the sample: HTTP {resp.status_code} {resp.text[:200]}")
    return False


def _synth_sample_sapi(dst: Path) -> bool:
    """Fallback: generate a wav with the Windows SAPI voice."""
    if os.name != "nt":
        return False
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{dst}'); "
        f"$s.Speak('{SAMPLE_SENTENCE}'); $s.Dispose();"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            timeout=60,
        )
        ok = dst.is_file() and dst.stat().st_size > 0
        if ok:
            print("  sample produced with Windows SAPI (fallback path)")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] SAPI could not produce the sample: {exc}")
        return False


def _mp3_to_webm_with_av(src: Path, dst: Path) -> bool:
    """Convert mp3 -> webm/opus with PyAV (no ffmpeg binary needed)."""
    try:
        import av
    except ImportError:
        return False
    try:
        with av.open(str(src)) as inp, av.open(str(dst), mode="w") as out:
            istream = next((s for s in inp.streams if s.type == "audio"), None)
            if istream is None:
                return False
            ostream = None
            for codec_name in ("libopus", "opus"):
                try:
                    ostream = out.add_stream(codec_name, rate=48000)
                    break
                except Exception:  # noqa: BLE001 — try the next encoder
                    ostream = None
            if ostream is None:
                return False
            ostream.layout = "stereo"
            fmt = ostream.codec_context.format
            resampler = av.AudioResampler(
                format=fmt.name if fmt else "s16", layout="stereo", rate=48000
            )
            for frame in inp.decode(istream):
                for resampled in resampler.resample(frame):
                    for packet in ostream.encode(resampled):
                        out.mux(packet)
            for resampled in resampler.resample(None):
                for packet in ostream.encode(resampled):
                    out.mux(packet)
            for packet in ostream.encode(None):
                out.mux(packet)
        return dst.is_file() and dst.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] PyAV conversion failed: {exc}")
        return False


def _mp3_to_webm_with_ffmpeg(src: Path, dst: Path) -> bool:
    """Convert with the ffmpeg CLI when it happens to be installed."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c:a", "libopus", "-b:a", "64k", str(dst)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return dst.is_file() and dst.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] ffmpeg conversion failed: {exc}")
        return False


def make_sample(client: TestClient) -> Path | None:
    """Build the sample: prefer /tts, then SAPI; convert to webm when possible."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    mp3 = WORK_DIR / "sample.mp3"
    wav = WORK_DIR / "sample_sapi.wav"
    if _synth_sample_via_api(client, mp3):
        source = mp3
    elif _synth_sample_sapi(wav):
        source = wav
    else:
        print("  [!] no sample could be produced; run test_voice.py path/to/audio.webm instead.")
        return None
    webm = WORK_DIR / "sample.webm"
    if _mp3_to_webm_with_av(source, webm) or _mp3_to_webm_with_ffmpeg(source, webm):
        print(f"  sample webm created: {webm}")
        return webm
    print("  [!] no webm/opus conversion available; testing STT with the source file.")
    return source


# --- tests -----------------------------------------------------------------

def test_stt(client: TestClient, sample: Path | None) -> bool:
    print("\n[1] STT — POST /stt")
    if sample is None or not sample.is_file():
        print("  FAIL no sample audio.")
        return False

    size = sample.stat().st_size
    print(f"  file: {sample.name}, {size} bytes")
    started = time.perf_counter()
    with sample.open("rb") as handle:
        resp = client.post(
            "/stt",
            files={"audio": (sample.name, handle, "audio/webm")},
        )
    elapsed = time.perf_counter() - started

    if resp.status_code != 200:
        print(f"  FAIL HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    data = resp.json()
    text = str(data.get("text", ""))
    duration = float(data.get("duration_s", 0.0))
    print(f"  text     : {text!r}")
    print(f"  language : {data.get('language')} | duration: {duration:.2f}s | elapsed: {elapsed:.2f}s")
    if duration >= 4.0:
        status = "OK" if elapsed < 3.0 else "SLOW — note it in the README"
        print(f"  target   : < 3.0s for a 5s clip -> {status} ({elapsed:.2f}s)")

    if not text.strip():
        print("  FAIL empty transcript.")
        return False

    # Warm measurement: model already in memory, same process.
    started = time.perf_counter()
    with sample.open("rb") as handle:
        resp_warm = client.post(
            "/stt",
            files={"audio": (sample.name, handle, "audio/webm")},
        )
    warm_elapsed = time.perf_counter() - started
    warm_ok = resp_warm.status_code == 200
    print(
        f"  warm     : {warm_elapsed:.2f}s (model in memory; target < 3.0s -> "
        f"{'OK' if warm_elapsed < 3.0 else 'SLOW'})"
    )

    # Empty audio -> {"text": ""}
    resp_empty = client.post(
        "/stt",
        files={"audio": ("empty.webm", b"", "audio/webm")},
    )
    empty_ok = resp_empty.status_code == 200 and resp_empty.json().get("text") == ""
    print(f"  empty    : {'OK' if empty_ok else 'FAIL ' + resp_empty.text[:200]}")
    return bool(text.strip()) and empty_ok and warm_ok


def test_tts(client: TestClient) -> bool:
    print("\n[2] TTS — POST /tts")
    payload = {"text": "**Merhaba!** Bu bir testtir 🎧 https://ornek.com"}
    started = time.perf_counter()
    resp = client.post("/tts", json=payload)
    elapsed = time.perf_counter() - started

    if resp.status_code != 200:
        print(f"  FAIL HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    media_type = resp.headers.get("content-type", "")
    if "audio/mpeg" not in media_type:
        print(f"  FAIL content-type: {media_type}")
        return False
    if len(resp.content) < 1000:
        print(f"  FAIL mp3 too small: {len(resp.content)} bytes")
        return False

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out = WORK_DIR / "answer.mp3"
    out.write_bytes(resp.content)
    print(f"  mp3      : {out} ({len(resp.content)} bytes, {elapsed:.2f}s)")

    # The second call must be served from the cache.
    started = time.perf_counter()
    resp_cached = client.post("/tts", json=payload)
    cached_elapsed = time.perf_counter() - started
    cache_ok = resp_cached.status_code == 200 and len(resp_cached.content) == len(resp.content)
    print(f"  cache    : {'OK' if cache_ok else 'FAIL'} ({cached_elapsed:.3f}s)")

    # Empty text must be rejected with 400, not 500.
    resp_blank = client.post("/tts", json={"text": "   ***   "})
    blank_ok = resp_blank.status_code == 400
    print(f"  blank    : {'OK' if blank_ok else 'FAIL'} (HTTP {resp_blank.status_code})")

    # Markdown/URL/emoji must be stripped before synthesis.
    from backend.voice.tts import clean_text  # noqa: PLC0415

    cleaned = clean_text(str(payload["text"]))
    clean_ok = "http" not in cleaned and "*" not in cleaned and "🎧" not in cleaned
    print(f"  cleaning : {'OK' if clean_ok else 'FAIL'} -> {cleaned!r}")

    return cache_ok and blank_ok and clean_ok


def main() -> int:
    load_env()
    print("Person 5 — voice test (STT + TTS)")
    print(f"repo: {ROOT}")
    print("The first run may download the faster-whisper model; please wait...")

    app = FastAPI()
    app.include_router(stt_router)
    app.include_router(tts_router)
    client = TestClient(app)

    sample_arg = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    sample = sample_arg if sample_arg else make_sample(client)

    results = {
        "stt": test_stt(client, sample),
        "tts": test_tts(client),
    }

    print("\n=== SUMMARY ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
