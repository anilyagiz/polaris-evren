"""POST /stt — transcribe an uploaded recording (owner: Person 5 / K5).

- faster-whisper, STT_MODEL (default ``small``), device="cpu", compute_type="int8".
- The model is loaded once on the first request and kept in memory.
- language="tr", vad_filter=True, beam_size=1 (latency over marginal accuracy).
- Input : multipart, field name ``audio`` (the extension sends audio/webm).
- Output: {"text", "language", "duration_s"}; empty audio -> {"text": ""}.

Latency note: handing CTranslate2 the logical core count causes
oversubscription and *slows it down*, so the default is the physical core
count (logical / 2). Override with the STT_CPU_THREADS environment variable.

K3 wiring in main.py:
    from backend.voice.stt import router as stt_router
    app.include_router(stt_router)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.db import load_env

logger = logging.getLogger(__name__)

router = APIRouter()

# Single model instance kept in memory (the heavy import happens on first use).
_MODEL: Any = None
_MODEL_SIZE: str | None = None
_MODEL_THREADS: int | None = None
_LOAD_LOCK = threading.Lock()   # guards model loading (no double load)
_RUN_LOCK = threading.Lock()    # one transcription at a time (model not re-entrant)

MAX_BYTES = 10 * 1024 * 1024
MIN_BYTES = 800                 # anything smaller cannot hold real speech

_AUDIO_SUFFIXES = {".webm", ".ogg", ".opus", ".mp3", ".wav", ".m4a", ".mp4", ".flac"}
_CT_SUFFIX = {
    "webm": ".webm", "ogg": ".ogg", "wav": ".wav", "x-wav": ".wav", "wave": ".wav",
    "mp4": ".mp4", "m4a": ".m4a", "x-m4a": ".m4a", "mpeg": ".mp3", "mp3": ".mp3",
    "flac": ".flac", "opus": ".opus",
}


def _model_size() -> str:
    load_env()
    return (os.environ.get("STT_MODEL") or "small").strip() or "small"


def _cpu_threads() -> int:
    """Number of threads to hand to CTranslate2.

    Defaults to the physical core count: os.cpu_count() reports logical cores,
    so it is halved here (16 -> 8 on a Ryzen 7 4800H). Giving CTranslate2 all
    logical cores measurably slows transcription down.
    """
    load_env()
    raw = (os.environ.get("STT_CPU_THREADS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    logical = os.cpu_count() or 4
    return max(1, logical // 2)


def _get_model() -> Any:
    """Load the faster-whisper model once (CPU, int8) and reuse it."""
    global _MODEL, _MODEL_SIZE, _MODEL_THREADS
    size = _model_size()
    threads = _cpu_threads()
    if _MODEL is not None and _MODEL_SIZE == size and _MODEL_THREADS == threads:
        return _MODEL
    with _LOAD_LOCK:
        if _MODEL is None or _MODEL_SIZE != size or _MODEL_THREADS != threads:
            from faster_whisper import WhisperModel  # heavy import, deferred

            logger.info("loading faster-whisper %s (cpu, int8, %d threads)", size, threads)
            _MODEL = WhisperModel(
                size,
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
                num_workers=1,
            )
            _MODEL_SIZE = size
            _MODEL_THREADS = threads
            logger.info("faster-whisper %s ready", size)
    return _MODEL


def warmup() -> None:
    """Load the model in a background thread (non-blocking)."""
    def _run() -> None:
        try:
            _get_model()
        except Exception as exc:  # noqa: BLE001
            logger.warning("STT warm-up failed: %s", exc)

    threading.Thread(target=_run, name="stt-warmup", daemon=True).start()


if (os.environ.get("STT_WARMUP") or "").strip() == "1":
    warmup()


def _transcribe_sync(path: str) -> tuple[str, str, float]:
    """Transcribe a file: (text, language, duration_s)."""
    model = _get_model()
    with _RUN_LOCK:
        return _run_model(model, path)


def _run_model(model: Any, path: str) -> tuple[str, str, float]:
    segments, info = model.transcribe(
        path,
        language="tr",
        vad_filter=True,
        beam_size=1,                       # latency
        temperature=0.0,                   # deterministic, skips the fallback sweep
        without_timestamps=True,           # timestamps are useless for short clips
        condition_on_previous_text=False,  # avoids error propagation on short clips
    )
    parts: list[str] = []
    last_end = 0.0
    for segment in segments:
        parts.append(segment.text.strip())
        last_end = max(last_end, float(getattr(segment, "end", 0.0) or 0.0))
    text = " ".join(p for p in parts if p).strip()
    duration = float(getattr(info, "duration", 0.0) or 0.0) or last_end
    language = str(getattr(info, "language", "") or "tr")
    return text, language, duration


def _suffix_for(audio: UploadFile) -> str:
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix in _AUDIO_SUFFIXES:
        return suffix
    ct = (audio.content_type or "").lower().split(";")[0].strip()
    sub = ct.split("/")[-1] if "/" in ct else ""
    return _CT_SUFFIX.get(sub, ".webm")


@router.post("/stt")
async def stt(audio: UploadFile = File(...)) -> dict[str, Any]:
    """Transcribe an uploaded recording to text."""
    load_env()
    data = await audio.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ses dosyası çok büyük (en fazla {MAX_BYTES // (1024 * 1024)} MB).",
        )
    if len(data) < MIN_BYTES:
        return {"text": "", "language": "tr", "duration_s": 0.0}

    try:
        import faster_whisper  # noqa: F401
        import av  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Ses tanıma kullanılamıyor: faster-whisper/av yüklü değil "
                   "(pip install faster-whisper av).",
        ) from exc

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_for(audio)) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        text, language, duration = await asyncio.to_thread(_transcribe_sync, tmp_path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — answer 500 instead of crashing the app
        logger.exception("STT failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ses metne çevrilemedi: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    logger.info("STT: %.2fs audio -> %d chars", duration, len(text))
    return {"text": text, "language": language, "duration_s": round(duration, 2)}
