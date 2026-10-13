"""POST /tts — turn text into an mp3 (owner: Person 5 / K5).

- edge-tts, voice TTS_VOICE (default tr-TR-EmelNeural).
- Input: JSON {"text"}; output: audio/mpeg.
- Markdown, URLs and emoji are stripped; the text is capped at 600 characters.
- Cache: backend/data/tts_cache/{sha1}.mp3.
- edge-tts is an unofficial client and needs the internet. If it fails the
  endpoint answers 503 and the panel falls back to the browser voice — that is
  the fallback path the contract specifies, so no second server-side engine is
  used (gTTS would pin click<8.2 and break huggingface-hub / faster-whisper).

Engine order:
  1. edge-tts against its own (DRM / Sec-MS-GEC aware) endpoint
  2. edge-tts against a patched endpoint, only if step 1 failed. Never on
     timeout: swapping endpoints does not fix a timeout.
  3. 503

K3 wiring in main.py:
    from backend.voice.tts import router as tts_router
    app.include_router(tts_router)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from backend.db import DATA_DIR, load_env

logger = logging.getLogger(__name__)

router = APIRouter()

TTS_CACHE_DIR = DATA_DIR / "tts_cache"
MAX_CHARS = 600
_ENGINE_TIMEOUT_S = 30.0
_RETRY_SLEEP_S = 2.0

# --- text cleaning ---------------------------------------------------------
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")           # [text](url) -> text
_MD_RE = re.compile(r"[*_`~#>|]+")                           # markdown marks
_LIST_RE = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)  # bullet markers
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"   # flags
    "\U0001F300-\U0001FAFF"   # emoji blocks
    "\U00002600-\U000027BF"   # symbols
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0000200D"              # zero width joiner
    "]+"
)


class TtsRequest(BaseModel):
    """POST /tts body."""
    text: str = Field(..., description="Seslendirilecek metin")


def clean_text(text: str) -> str:
    """Strip markdown, URLs and emoji; collapse whitespace; cap at MAX_CHARS."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)
    text = _MD_RE.sub(" ", text)
    text = _LIST_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        space = cut.rfind(" ")
        if space > MAX_CHARS // 2:
            cut = cut[:space]
        text = cut.rstrip() + "…"
    return text


def _voice() -> str:
    load_env()
    return (os.environ.get("TTS_VOICE") or "tr-TR-EmelNeural").strip() or "tr-TR-EmelNeural"


_EDGE_WORKAROUND_DONE = False


def _apply_edge_workaround() -> None:
    """Point edge-tts at a fallback endpoint — last resort, not the default.

    Microsoft occasionally moves the Edge "Read Aloud" websocket and the old
    address starts answering 403. This is only called after the default
    endpoint has already failed, so a working installation is never disturbed
    up front.

    NOTE: ``edge_tts.communicate.WSS_URL`` must be patched — the library does
    ``from .constants import WSS_URL``, so changing ``constants`` has no
    effect. Harmless on library versions that no longer need it.
    Reference: https://github.com/rany2/edge-tts/issues/490
    """
    global _EDGE_WORKAROUND_DONE
    if _EDGE_WORKAROUND_DONE:
        return
    try:
        from edge_tts import communicate as edge_communicate
        from edge_tts.constants import TRUSTED_CLIENT_TOKEN

        edge_communicate.WSS_URL = (
            "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
            f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}"
        )
        _EDGE_WORKAROUND_DONE = True
        logger.warning("edge-tts fallback endpoint enabled (issue #490)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not enable the edge-tts fallback endpoint: %s", exc)


def _cache_path(text: str, voice: str) -> Path:
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / f"{key}.mp3"


def _cleanup(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


async def _edge_attempt(text: str, voice: str, target: Path) -> None:
    """One edge-tts synthesis into ``target``. Raises on failure."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await asyncio.wait_for(communicate.save(str(target)), timeout=_ENGINE_TIMEOUT_S)


async def _synthesize(text: str, voice: str) -> bytes:
    """Produce mp3 bytes, or raise HTTPException(503) when edge-tts cannot."""
    try:
        import edge_tts  # noqa: F401 — probe availability before starting
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="edge-tts yüklü değil (pip install edge-tts). Panel tarayıcı sesine geçebilir.",
        ) from exc

    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _cache_path(text, voice)
    if target.is_file() and target.stat().st_size > 0:
        return target.read_bytes()

    last_error: Exception | None = None
    timed_out = False

    for use_patched in (False, True):
        if use_patched:
            _apply_edge_workaround()
        # Unique temporary name: two concurrent requests for the same text must
        # not write over each other and produce a corrupt mp3.
        tmp = target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            await _edge_attempt(text, voice, tmp)
            os.replace(tmp, target)
            return target.read_bytes()
        except asyncio.TimeoutError as exc:
            last_error, timed_out = exc, True
        except Exception as exc:  # noqa: BLE001 — unofficial client, needs the internet
            last_error = exc
        finally:
            _cleanup(tmp)

        if timed_out or use_patched:
            break

        status = getattr(last_error, "status", None)
        logger.warning("edge-tts first attempt failed (%s); trying the fallback endpoint", last_error)
        if status == 429:
            await asyncio.sleep(_RETRY_SLEEP_S)

    if timed_out:
        logger.warning("TTS timed out after %.0fs", _ENGINE_TIMEOUT_S)
        raise HTTPException(
            status_code=503,
            detail="Seslendirme zaman aşımına uğradı. Panel tarayıcı sesine geçebilir.",
        ) from last_error
    logger.warning("TTS failed: %s", last_error)
    raise HTTPException(
        status_code=503,
        detail="Seslendirme servisi şu an kullanılamıyor (edge-tts/internet). "
               "Panel tarayıcı sesine geçebilir.",
    ) from last_error


@router.post("/tts")
async def tts(payload: TtsRequest) -> Response:
    """Return the spoken text as audio/mpeg."""
    load_env()
    text = clean_text(payload.text)
    if not text:
        raise HTTPException(status_code=400, detail="Seslendirilecek metin boş.")

    voice = _voice()
    audio = await _synthesize(text, voice)
    logger.info("TTS: %d chars -> %d bytes (voice: %s)", len(text), len(audio), voice)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )
