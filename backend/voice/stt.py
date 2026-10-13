"""Speech-to-text endpoint: POST /stt (multipart field "audio") -> {"text", "language", "duration_s"}."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, File, HTTPException, UploadFile

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

router = APIRouter()

_model: WhisperModel | None = None
_model_lock = asyncio.Lock()


def _load_model() -> WhisperModel:
    from faster_whisper import WhisperModel

    name = os.getenv("STT_MODEL", "small")
    started = time.perf_counter()
    model = WhisperModel(name, device="cpu", compute_type="int8")
    logger.info("Whisper model '%s' loaded in %.1fs", name, time.perf_counter() - started)
    return model


async def get_model() -> WhisperModel:
    """Load the Whisper model once (on first use) and keep it in memory."""
    global _model
    if _model is None:
        async with _model_lock:
            if _model is None:
                _model = await asyncio.to_thread(_load_model)
    return _model


def _transcribe_file(model: WhisperModel, path: str) -> dict:
    segments, info = model.transcribe(path, language="tr", vad_filter=True, beam_size=1)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "duration_s": round(float(info.duration), 2),
    }


async def transcribe_bytes(data: bytes, suffix: str = ".webm") -> dict:
    """Transcribe raw audio bytes (webm/opus, wav, mp3 ...). Usable without HTTP."""
    if not data:
        return {"text": "", "language": "tr", "duration_s": 0.0}

    model = await get_model()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
        started = time.perf_counter()
        result = await asyncio.to_thread(_transcribe_file, model, tmp_path)
        logger.info(
            "Transcribed %.2fs audio in %.2fs: %r",
            result["duration_s"],
            time.perf_counter() - started,
            result["text"][:80],
        )
        return result
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            logger.warning("Could not delete temp file %s", tmp_path)


@router.post("/stt")
async def stt(audio: UploadFile = File(...)) -> dict:
    data = await audio.read()
    suffix = Path(audio.filename or "").suffix or ".webm"
    try:
        return await transcribe_bytes(data, suffix=suffix)
    except Exception as exc:  # decoding errors, corrupt uploads
        logger.exception("STT failed")
        raise HTTPException(status_code=400, detail="Ses dosyası çözümlenemedi.") from exc
