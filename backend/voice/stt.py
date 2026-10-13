"""POST /stt — ses dosyasını metne çevirir (Kişi 5 sahibi).

- faster-whisper, STT_MODEL (varsayılan small), device="cpu", compute_type="int8"
- Model uygulama ilk istekte bir kez yüklenir ve bellekte kalır.
- language="tr", vad_filter=True, beam_size=1 (hız için).
- Girdi: multipart, alan adı "audio" (extension audio/webm gönderir).
- Çıktı: {"text", "language", "duration_s"}; boş ses → {"text": ""}.

Hız notu: CTranslate2'ye mantıksal çekirdek sayısını vermek oversubscription
yapıp yavaşlatır; bu yüzden varsayılan olarak fiziksel çekirdek (mantıksal/2)
kullanılır. STT_CPU_THREADS ortam değişkeni ile elle ayarlanabilir.

K3 main.py'de:
    from backend.voice.stt import router as stt_router
    app.include_router(stt_router)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.db import load_env

logger = logging.getLogger(__name__)

router = APIRouter()

# Model tek örnek olarak bellekte tutulur (ağırlıklı import ilk istekte yapılır).
_MODEL: Any = None
_MODEL_SIZE: str | None = None
_MODEL_THREADS: int | None = None
_MODEL_LOCK = asyncio.Lock()

_WEBM_SUFFIXES = {".webm", ".ogg", ".opus", ".mp3", ".wav", ".m4a", ".mp4", ".flac"}


def _model_size() -> str:
    load_env()
    return (os.environ.get("STT_MODEL") or "small").strip() or "small"


def _cpu_threads() -> int:
    """CTranslate2 için iş parçacığı sayısı.

    Varsayılan: fiziksel çekirdek sayısı (os.cpu_count() mantıksal döner,
    bu yüzden ikiye bölünür). Ryzen 7 4800H'de 16 -> 8.
    """
    load_env()
    raw = (os.environ.get("STT_CPU_THREADS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    logical = os.cpu_count() or 4
    return max(1, logical // 2)


def _get_model() -> Any:
    """faster-whisper modelini bir kez yükler (CPU, int8)."""
    global _MODEL, _MODEL_SIZE, _MODEL_THREADS
    size = _model_size()
    threads = _cpu_threads()
    if _MODEL is None or _MODEL_SIZE != size or _MODEL_THREADS != threads:
        from faster_whisper import WhisperModel  # ağır import; ilk istekte

        logger.info(
            "faster-whisper modeli yükleniyor: %s (cpu, int8, %d iş parçacığı)", size, threads
        )
        _MODEL = WhisperModel(
            size,
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            num_workers=1,
        )
        _MODEL_SIZE = size
        _MODEL_THREADS = threads
        logger.info("faster-whisper modeli hazır: %s", size)
    return _MODEL


def _transcribe_sync(path: str) -> tuple[str, str, float]:
    """Dosyayı metne çevirir: (text, language, duration_s)."""
    model = _get_model()
    segments, info = model.transcribe(
        path,
        language="tr",
        vad_filter=True,
        beam_size=1,                       # hız için
        temperature=0.0,                   # deterministik + fallback taraması yapmaz
        without_timestamps=True,           # zaman damgası üretimi kısa klipte gereksiz
        condition_on_previous_text=False,  # kısa kliplerde hız + hata yayılımını önler
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


@router.post("/stt")
async def stt(audio: UploadFile = File(...)) -> dict[str, Any]:
    """Ses dosyasını metne çevirir."""
    load_env()
    data = await audio.read()
    if not data:
        # Boş ses
        return {"text": "", "language": "tr", "duration_s": 0.0}

    filename = audio.filename or "audio.webm"
    suffix = Path(filename).suffix.lower()
    if suffix not in _WEBM_SUFFIXES:
        suffix = ".webm"

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        # Modeli tek iş parçacığında tut (aynı anda iki transkripsiyon yok).
        async with _MODEL_LOCK:
            text, language, duration = await asyncio.to_thread(_transcribe_sync, tmp_path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 500 olarak cevapla, çökertme
        logger.exception("STT hatası: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ses metne çevrilemedi: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    logger.info("STT: %.2fs ses -> %d karakter", duration, len(text))
    return {"text": text, "language": language, "duration_s": round(duration, 2)}
