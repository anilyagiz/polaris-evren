"""POST /tts — turn text into speech audio (owner: Person 5 / K5).

- Input: JSON {"text"}; output: audio/mpeg (mp3; EMA renders 24 kHz mono).
- Markdown, URLs and emoji are stripped; the text is capped at 600 characters.
- Cache: backend/data/tts_cache/{sha1}.mp3 (or .wav); key = engine|voice|speed|format|text.

Engines (TTS_ENGINE=ema|edge, default ema):
  * ema  — EMA Lightning (canberkkkkkk/ema-lightning, Apache-2.0): offline
    8.6M-parameter Turkish model on torch. Loaded lazily once (threading.Lock),
    synthesised in a worker thread, encoded to mp3 with PyAV (libmp3lame); if
    no mp3 encoder is available the response is audio/wav. If the package or
    torch is missing, or synthesis fails, the request falls back to edge-tts
    (logged once).
  * edge — edge-tts, voice TTS_VOICE (default tr-TR-EmelNeural). Unofficial
    client, needs the internet:
      1. its own (DRM / Sec-MS-GEC aware) endpoint
      2. a patched endpoint, only if step 1 failed (never on timeout)
      3. 503 -> the panel falls back to the browser voice.
TTS_SPEED (0.25-4, default 1.0) sets the EMA speaking speed.
TTS_WARMUP=1 (or STT_WARMUP=1) loads the EMA model in the background at import.

K3 wiring in main.py:
    from backend.voice.tts import router as tts_router
    app.include_router(tts_router)
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import os
import re
import threading
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

ENGINES = ("ema", "edge")
EMA_SAMPLE_RATE = 24000      # 24 kHz mono keeps the payload small
EMA_SEED = 0                 # fixed seed: same text -> same voice (and cacheable)
EMA_MP3_BITRATE = 64000
_MEDIA_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav"}

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


def tts_engine() -> str:
    """Configured engine: TTS_ENGINE=ema|edge (default ema)."""
    load_env()
    value = (os.environ.get("TTS_ENGINE") or "ema").strip().lower()
    if value not in ENGINES:
        logger.warning("unknown TTS_ENGINE=%r, using ema", value)
        return "ema"
    return value


def tts_speed() -> float:
    """TTS_SPEED clamped to EMA's 0.25-4 range (default 1.0)."""
    load_env()
    try:
        value = float((os.environ.get("TTS_SPEED") or "1.0").strip())
    except ValueError:
        return 1.0
    return min(4.0, max(0.25, value))


def _cache_path(text: str, voice: str, engine: str = "edge", speed: float = 1.0,
                fmt: str = "mp3") -> Path:
    raw = f"{engine}|{voice}|{speed:g}|{fmt}|{text}"
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / f"{key}.{fmt}"


# --- EMA Lightning (offline) ---------------------------------------------------
_EMA_MODEL: Any = None
_EMA_LOCK = threading.Lock()
_EMA_DISABLED = False        # set once ema_lightning/torch cannot be imported or loaded
_EMA_WARNED = False
_MP3_OK: bool | None = None


class _WavInstead(Exception):
    """Raised by the worker when mp3 encoding failed but wav bytes are ready."""

    def __init__(self, data: bytes) -> None:
        super().__init__("wav")
        self.data = data


def _ema_warn_once(msg: str, *args: Any) -> None:
    global _EMA_WARNED
    if not _EMA_WARNED:
        _EMA_WARNED = True
        logger.warning(msg, *args)


def ema_importable() -> bool:
    """Is ema_lightning installed?  Cheap: does not import torch."""
    try:
        return importlib.util.find_spec("ema_lightning") is not None
    except (ImportError, ValueError):
        return False


def _get_ema() -> Any:
    """Load the EMA model once (thread-safe). Raises on failure."""
    global _EMA_MODEL, _EMA_DISABLED
    if _EMA_MODEL is not None:
        return _EMA_MODEL
    with _EMA_LOCK:
        if _EMA_MODEL is None:
            if _EMA_DISABLED:
                raise RuntimeError("EMA Lightning unavailable")
            try:
                from ema_lightning import EMA  # heavy: imports torch
            except Exception:  # package/torch missing or broken: stop trying
                _EMA_DISABLED = True
                raise
            # Downloads ema.pt + decoder.pt (~34 MB) on first use; a network
            # error here is retried on the next request.
            model = EMA()
            # On CPU the library otherwise spends minutes measuring the "best"
            # batch size on the first say(); one request at a time needs 1.
            if getattr(model, "_batch_size", 0) is None:
                model._batch_size = 1
            _EMA_MODEL = model
            logger.info("EMA Lightning TTS ready (device: %s)", getattr(model, "device", "?"))
    return _EMA_MODEL


def _pcm16(audio: Any) -> Any:
    import numpy as np

    return (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")


def _encode_mp3(audio: Any, sample_rate: int) -> bytes:
    """float32 mono [-1, 1] -> mp3 bytes via PyAV (libmp3lame)."""
    import io

    import av

    pcm = _pcm16(audio)
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=sample_rate)
        stream.layout = "mono"
        stream.bit_rate = EMA_MP3_BITRATE
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()


def _encode_wav(audio: Any, sample_rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(_pcm16(audio).tobytes())
    return buf.getvalue()


def _ema_format() -> str:
    """mp3 when PyAV can encode it, else wav. Probed once."""
    global _MP3_OK
    if _MP3_OK is None:
        try:
            import av

            av.codec.Codec("libmp3lame", "w")
            _MP3_OK = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("mp3 encoder unavailable (%s); EMA TTS will return audio/wav", exc)
            _MP3_OK = False
    return "mp3" if _MP3_OK else "wav"


def _ema_render(text: str, speed: float, fmt: str) -> bytes:
    """Blocking: synthesise with EMA and encode. Runs in a worker thread."""
    model = _get_ema()
    speech = model.say(text, speed=speed, seed=EMA_SEED, sample_rate=EMA_SAMPLE_RATE)
    if fmt == "mp3":
        try:
            return _encode_mp3(speech.audio, speech.sample_rate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mp3 encoding failed (%s); returning wav", exc)
            raise _WavInstead(_encode_wav(speech.audio, speech.sample_rate)) from exc
    return _encode_wav(speech.audio, speech.sample_rate)


def _store(target: Path, data: bytes) -> None:
    """Atomically write a cache entry (unique tmp name, then rename)."""
    tmp = target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("could not write TTS cache %s: %s", target.name, exc)
    finally:
        _cleanup(tmp)


async def _synthesize_ema(text: str) -> tuple[bytes, str] | None:
    """(audio, format) from EMA, or None when the caller should fall back to edge-tts."""
    if _EMA_DISABLED or not ema_importable():
        _ema_warn_once("EMA Lightning (ema-lightning/torch) not available; using edge-tts")
        return None
    speed = tts_speed()
    fmt = _ema_format()
    target = _cache_path(text, "ema-lightning", "ema", speed, fmt)
    if target.is_file() and target.stat().st_size > 0:
        return target.read_bytes(), fmt
    try:
        data = await asyncio.to_thread(_ema_render, text, speed, fmt)
    except _WavInstead as wav:
        data, fmt = wav.data, "wav"
        target = _cache_path(text, "ema-lightning", "ema", speed, fmt)
    except Exception as exc:  # noqa: BLE001 — any failure -> edge-tts
        if _EMA_DISABLED:
            _ema_warn_once("EMA Lightning could not be loaded (%s); using edge-tts", exc)
        else:
            logger.warning("EMA Lightning synthesis failed (%s); using edge-tts", exc)
        return None
    if not data:
        return None
    _store(target, data)
    return data, fmt


def tts_available() -> bool:
    """True if either engine can be tried (for /health)."""
    if not _EMA_DISABLED and ema_importable():
        return True
    try:
        return importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ValueError):
        return False


def active_engine() -> str:
    """The engine /tts tries first right now: "ema" or "edge"."""
    if tts_engine() == "ema" and not _EMA_DISABLED and ema_importable():
        return "ema"
    return "edge"


def warmup() -> None:
    """Load the EMA model (and run one tiny synthesis) in a background thread."""
    def _run() -> None:
        try:
            _get_ema().say("Merhaba.", seed=EMA_SEED, sample_rate=EMA_SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS warm-up failed: %s", exc)

    threading.Thread(target=_run, name="tts-warmup", daemon=True).start()


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
    """Produce edge-tts mp3 bytes, or raise HTTPException(503) when it cannot."""
    try:
        import edge_tts  # noqa: F401 — probe availability before starting
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="edge-tts yüklü değil (pip install edge-tts). Panel tarayıcı sesine geçebilir.",
        ) from exc

    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _cache_path(text, voice, "edge", 1.0, "mp3")
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
    """Return the spoken text as audio/mpeg (audio/wav if no mp3 encoder)."""
    load_env()
    text = clean_text(payload.text)
    if not text:
        raise HTTPException(status_code=400, detail="Seslendirilecek metin boş.")

    result = await _synthesize_ema(text) if tts_engine() == "ema" else None
    if result is not None:
        audio, fmt = result
        engine = "ema"
    else:
        voice = _voice()
        audio, fmt, engine = await _synthesize(text, voice), "mp3", f"edge:{voice}"
    logger.info("TTS: %d chars -> %d bytes %s (%s)", len(text), len(audio), fmt, engine)
    return Response(
        content=audio,
        media_type=_MEDIA_TYPES[fmt],
        headers={"Cache-Control": "private, max-age=3600"},
    )


load_env()
if any((os.environ.get(name) or "").strip() == "1" for name in ("TTS_WARMUP", "STT_WARMUP")):
    if tts_engine() == "ema" and ema_importable():
        warmup()
