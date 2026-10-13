#!/usr/bin/env python3
"""Kişi 5 — ses testi (STT + TTS).

Bir örnek ses dosyasını metne, bir Türkçe cümleyi mp3'e çevirir.
Bağımsız çalışır: FastAPI uygulamasını bellekte kurup TestClient ile çağırır.

Kullanım:
    python scripts/test_voice.py                 # örnek sesi kendisi üretir (edge-tts)
    python scripts/test_voice.py yol/ara.webm    # kendi örnek dosyanı verir

Not: İlk çalıştırmada faster-whisper modeli (~460 MB, "small") indirilir.
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

# Windows konsolu cp1252 olabilir; Türkçe çıktı çökmesin diye UTF-8'e çevir.
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("_", "-") != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("chcp 65001 >NUL")  # konsol kod sayfası

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db import load_env  # noqa: E402
from backend.voice.stt import router as stt_router  # noqa: E402
from backend.voice.tts import router as tts_router  # noqa: E402

SAMPLE_SENTENCE = "Merhaba, bu bir ses testi. Kişi beşin ses modülü çalışıyor."
WORK_DIR = ROOT / "backend" / "data" / "test_voice"


# --- Örnek ses üretimi -----------------------------------------------------

def _synth_sample_via_api(client: TestClient, dst: Path) -> bool:
    """Örnek sesi gerçek /tts ucundan üretir (demo ile tam aynı yol)."""
    resp = client.post("/tts", json={"text": SAMPLE_SENTENCE})
    if resp.status_code == 200 and len(resp.content) > 1000:
        dst.write_bytes(resp.content)
        print(f"  örnek mp3 /tts ucundan üretildi ({len(resp.content)} bayt)")
        return True
    print(f"  [!] /tts örnek üretilemedi: HTTP {resp.status_code} {resp.text[:200]}")
    return False


def _synth_sample_sapi(dst: Path) -> bool:
    """Yedek: Windows SAPI ile wav üretir (edge-tts erişilemezse)."""
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
            print("  örnek ses Windows SAPI ile üretildi (yedek yol)")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] SAPI örnek üretilemedi: {exc}")
        return False


def _mp3_to_webm_with_av(src: Path, dst: Path) -> bool:
    """PyAV ile mp3 -> webm/opus dönüşümü (ffmpeg gerekmez)."""
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
                except Exception:  # noqa: BLE001 — kodlayıcı yoksa sıradakini dene
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
        print(f"  [!] PyAV dönüşümü başarısız: {exc}")
        return False


def _mp3_to_webm_with_ffmpeg(src: Path, dst: Path) -> bool:
    """Sistemde ffmpeg varsa CLI ile dönüşüm."""
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
        print(f"  [!] ffmpeg dönüşümü başarısız: {exc}")
        return False


def make_sample(client: TestClient) -> Path | None:
    """Örnek sesi üretir: önce /tts, olmazsa SAPI; mümkünse webm'e çevirir."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    mp3 = WORK_DIR / "ornek.mp3"
    wav = WORK_DIR / "ornek_sapi.wav"
    if _synth_sample_via_api(client, mp3):
        source = mp3
    elif _synth_sample_sapi(wav):
        source = wav
    else:
        print("  [!] örnek ses üretilemedi; test_voice.py yol/ara.webm ile çalıştırılabilir.")
        return None
    webm = WORK_DIR / "ornek.webm"
    if _mp3_to_webm_with_av(source, webm) or _mp3_to_webm_with_ffmpeg(source, webm):
        print(f"  örnek webm üretildi: {webm}")
        return webm
    print("  [!] webm/opus dönüşümü yok; STT kaynak dosya üzerinden test edilecek.")
    return source


# --- Testler --------------------------------------------------------------

def test_stt(client: TestClient, sample: Path | None) -> bool:
    print("\n[1] STT — POST /stt")
    if sample is None or not sample.is_file():
        print("  FAIL örnek ses yok.")
        return False

    size = sample.stat().st_size
    print(f"  dosya: {sample.name}, {size} bayt")
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
    print(f"  metin   : {text!r}")
    print(f"  dil     : {data.get('language')} | süre: {duration:.2f}s | işlem: {elapsed:.2f}sn")
    if duration >= 4.0:
        status = "TAMAM" if elapsed < 3.0 else "YAVAŞ — README'ye not ekle"
        print(f"  hedef   : 5 sn ses için < 3.0 sn -> {status} ({elapsed:.2f} sn)")

    if not text.strip():
        print("  FAIL boş metin döndü.")
        return False

    # Isınmış ölçüm: model yükleme hariç ikinci çağrı (aynı süreçte).
    started = time.perf_counter()
    with sample.open("rb") as handle:
        resp_warm = client.post(
            "/stt",
            files={"audio": (sample.name, handle, "audio/webm")},
        )
    warm_elapsed = time.perf_counter() - started
    warm_ok = resp_warm.status_code == 200
    print(
        f"  warm   : {warm_elapsed:.2f} sn (model bellekte; hedef < 3.0 sn -> "
        f"{'TAMAM' if warm_elapsed < 3.0 else 'YAVAŞ'})"
    )

    # Boş ses → {"text": ""}
    resp_empty = client.post(
        "/stt",
        files={"audio": ("bos.webm", b"", "audio/webm")},
    )
    empty_ok = resp_empty.status_code == 200 and resp_empty.json().get("text") == ""
    print(f"  boş ses : {'TAMAM' if empty_ok else 'FAIL ' + resp_empty.text[:200]}")
    return bool(text.strip()) and empty_ok and warm_ok


def test_tts(client: TestClient) -> bool:
    print("\n[2] TTS — POST /tts")
    started = time.perf_counter()
    resp = client.post("/tts", json={"text": f"**Merhaba!** Bu bir testtir 🎧 https://ornek.com"})
    elapsed = time.perf_counter() - started

    if resp.status_code != 200:
        print(f"  FAIL HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    media_type = resp.headers.get("content-type", "")
    if "audio/mpeg" not in media_type:
        print(f"  FAIL content-type: {media_type}")
        return False
    if len(resp.content) < 1000:
        print(f"  FAIL çok küçük mp3: {len(resp.content)} bayt")
        return False

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out = WORK_DIR / "cevap.mp3"
    out.write_bytes(resp.content)
    print(f"  mp3     : {out} ({len(resp.content)} bayt, {elapsed:.2f} sn)")

    # Önbellek ikinci çağrıda hızlı olmalı.
    started = time.perf_counter()
    resp_cached = client.post("/tts", json={"text": "**Merhaba!** Bu bir testtir 🎧 https://ornek.com"})
    cached_elapsed = time.perf_counter() - started
    cache_ok = resp_cached.status_code == 200 and len(resp_cached.content) == len(resp.content)
    print(f"  önbellek: {'TAMAM' if cache_ok else 'FAIL'} ({cached_elapsed:.3f} sn)")
    return cache_ok


def main() -> int:
    load_env()
    print("Kişi 5 — ses testi (STT + TTS)")
    print(f"repo: {ROOT}")
    print("İlk çalıştırmada faster-whisper modeli indirilebilir; lütfen bekleyin...")

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

    print("\n=== ÖZET ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
