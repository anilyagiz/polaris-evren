"""Voice smoke test.

Usage (from repo root):
    python scripts/test_voice.py                 # in-process STT
    python scripts/test_voice.py --http http://localhost:8000   # also hit the running server
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

logger = logging.getLogger(__name__)

SAMPLE_SENTENCE = "Merhaba, yarın saat onda toplantıyı hatırlat."
OUT_DIR = ROOT / "backend" / "data" / "test_voice"


async def _edge_tts_mp3(text: str) -> bytes:
    import edge_tts

    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, "tr-TR-EmelNeural").stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def _gtts_mp3(text: str) -> bytes:
    from gtts import gTTS  # optional, test-only: pip install gTTS

    buf = io.BytesIO()
    gTTS(text, lang="tr").write_to_fp(buf)
    return buf.getvalue()


async def _synthesize_sample_mp3(text: str) -> bytes:
    try:
        return await _edge_tts_mp3(text)
    except Exception as exc:
        print(f"[sample] edge-tts başarısız ({exc.__class__.__name__}), gTTS deneniyor")
    try:
        return await asyncio.to_thread(_gtts_mp3, text)
    except ImportError as exc:
        raise SystemExit(
            "Örnek ses üretilemedi. 'pip install gTTS' kurun ya da --sample ile kendi webm kaydınızı verin."
        ) from exc


def mp3_to_webm_opus(mp3: bytes) -> bytes:
    """Re-encode mp3 to webm/opus with PyAV — the same container the panel's MediaRecorder sends."""
    import av

    out = io.BytesIO()
    with av.open(io.BytesIO(mp3)) as src, av.open(out, "w", format="webm") as dst:
        stream = dst.add_stream("libopus", rate=48000)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        for frame in src.decode(audio=0):
            for resampled in resampler.resample(frame):
                for packet in stream.encode(resampled):
                    dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    return out.getvalue()


async def make_sample_webm() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "sample.webm"
    if not path.exists():
        mp3 = await _synthesize_sample_mp3(SAMPLE_SENTENCE)
        path.write_bytes(mp3_to_webm_opus(mp3))
    return path


async def test_stt(sample: Path, expect_sentence: bool) -> None:
    from backend.voice import stt

    started = time.perf_counter()
    await stt.get_model()
    print(f"[stt] model yüklendi: {time.perf_counter() - started:.1f}s")

    for label, data in (("örnek", sample.read_bytes()), ("boş", b"")):
        started = time.perf_counter()
        result = await stt.transcribe_bytes(data)
        print(f"[stt] {label}: {result}  ({time.perf_counter() - started:.2f}s)")

    result = await stt.transcribe_bytes(sample.read_bytes())
    assert result["text"], "STT boş metin döndürdü"
    if expect_sentence:
        assert "toplantı" in result["text"].lower(), result["text"]


def test_stt_http(base_url: str, sample: Path) -> None:
    import httpx

    with httpx.Client(base_url=base_url, timeout=60) as client:
        started = time.perf_counter()
        r = client.post("/stt", files={"audio": ("rec.webm", sample.read_bytes(), "audio/webm")})
        print(f"[stt-http] {r.status_code} {r.json()}  ({time.perf_counter() - started:.2f}s)")
        r.raise_for_status()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", metavar="BASE_URL", help="also test the running server, e.g. http://localhost:8000")
    parser.add_argument("--sample", type=Path, help="own recording (webm/wav/mp3) instead of a synthesized one")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sample = args.sample or await make_sample_webm()
    print(f"[sample] {sample} ({sample.stat().st_size} bayt)")

    await test_stt(sample, expect_sentence=args.sample is None)
    if args.http:
        test_stt_http(args.http, sample)
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
