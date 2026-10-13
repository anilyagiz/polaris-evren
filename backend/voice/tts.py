"""Text-to-speech endpoint: POST /tts {"text"} -> audio/mpeg.

Primary engine is edge-tts (TTS_VOICE). If it fails and gTTS is installed, gTTS is used as a
fallback (not cached, so edge-tts output is preferred once it recovers). If every engine fails
the endpoint returns 503 and the panel falls back to the browser's own voice.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CHARS = 600
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "tts_cache"

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.MULTILINE)
_EMPHASIS = re.compile(r"[*_~]{1,3}")
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, symbols & pictographs ext.
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # flags
    "\U0000FE0F\U0000200D"   # variation selector, zero-width joiner
    "]+"
)
_WHITESPACE = re.compile(r"\s+")


class TTSRequest(BaseModel):
    text: str


def clean_text(text: str) -> str:
    """Strip markdown, URLs and emoji, collapse whitespace and cap at MAX_CHARS."""
    text = _CODE_BLOCK.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _TABLE_RULE.sub(" ", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    text = text.replace("|", ", ")
    text = _EMPHASIS.sub("", text)
    text = _EMOJI.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        sentence_end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if sentence_end > MAX_CHARS // 2:
            cut = cut[: sentence_end + 1]
        elif " " in cut:
            cut = cut[: cut.rfind(" ")]
        text = cut.strip()
    return text


def _voice() -> str:
    return os.getenv("TTS_VOICE", "tr-TR-EmelNeural")


def cache_path(text: str, voice: str) -> Path:
    digest = hashlib.sha1(f"{voice}\n{text}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.mp3"


async def _edge_tts(text: str, voice: str) -> bytes:
    import edge_tts

    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError("edge-tts returned no audio")
    return bytes(buf)


def _gtts(text: str) -> bytes:
    from gtts import gTTS

    buf = io.BytesIO()
    gTTS(text, lang="tr").write_to_fp(buf)
    return buf.getvalue()


async def synthesize(text: str) -> bytes:
    """Return mp3 bytes for already-cleaned text. Raises RuntimeError if every engine fails."""
    voice = _voice()
    path = cache_path(text, voice)
    if path.exists():
        logger.info("TTS cache hit %s", path.name)
        return path.read_bytes()

    started = time.perf_counter()
    try:
        audio = await asyncio.wait_for(_edge_tts(text, voice), timeout=15)
    except Exception as exc:
        logger.warning("edge-tts failed: %s", exc)
    else:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)
        logger.info("edge-tts synthesized %d chars in %.2fs", len(text), time.perf_counter() - started)
        return audio

    try:
        audio = await asyncio.wait_for(asyncio.to_thread(_gtts, text), timeout=15)
    except ImportError:
        logger.info("gTTS not installed; no TTS fallback")
    except Exception as exc:
        logger.warning("gTTS fallback failed: %s", exc)
    else:
        logger.info("gTTS fallback synthesized %d chars in %.2fs", len(text), time.perf_counter() - started)
        return audio

    raise RuntimeError("all TTS engines failed")


@router.post("/tts")
async def tts(req: TTSRequest) -> Response:
    text = clean_text(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="Okunacak metin yok.")
    try:
        audio = await synthesize(text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Ses servisine ulaşılamadı.") from exc
    return Response(content=audio, media_type="audio/mpeg")
