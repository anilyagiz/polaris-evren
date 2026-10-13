"""POST /tts — metni mp3'e çevirir (Kişi 5 sahibi).

- edge-tts, ses TTS_VOICE (varsayılan tr-TR-EmelNeural).
- Girdi: JSON {"text"}; Çıktı: audio/mpeg
- Markdown/URL/emoji temizlenir, metin 600 karakterle sınırlanır.
- backend/data/tts_cache/{sha1}.mp3 önbelleği.
- edge-tts resmî olmayan bir istemcidir ve internet ister; hata → 503
  (panel bu durumda tarayıcı sesine geçer).

Uç nokta notu: Önce edge-tts'in kendi (DRM/Sec-MS-GEC destekli) uç noktası
denenir. Yalnızca 403/429/ağ hatasında bir kez yedek uç nokta ile tekrarlanır.
Böylece Microsoft tarafındaki bir sorun çalışan varsayılanı bozmaz.

K3 main.py'de:
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
_TTS_TIMEOUT_S = 30.0
_RETRY_SLEEP_S = 2.0

# --- Metin temizleme -------------------------------------------------------
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")          # [metin](url) -> metin
_MD_RE = re.compile(r"[*_`~#>|]+")                          # markdown işaretleri
_LIST_RE = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)  # madde işaretleri
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"   # bayraklar
    "\U0001F300-\U0001FAFF"   # emoji blokları
    "\U00002600-\U000027BF"   # semboller
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0000200D"              # zero width joiner
    "]+"
)


class TtsRequest(BaseModel):
    """POST /tts gövdesi."""
    text: str = Field(..., description="Seslendirilecek metin")


def clean_text(text: str) -> str:
    """Markdown, URL ve emoji'yi temizler; 600 karakterde keser."""
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
    """edge-tts için YEDEK uç nokta düzeltmesi (varsayılan DEĞİL, son çare).

    Microsoft, Edge "Oku" (Read Aloud) WebSocket'ini zaman zaman taşıyor ve
    eski adrese yapılan el sıkışması 403 dönebiliyor. Bu fonksiyon **yalnızca
    normal deneme başarısız olduğunda** çağrılır; çalışan bir kurulumu
    bozmamak için peşinen uygulanmaz.

    DİKKAT: `edge_tts.communicate.WSS_URL` üzerine yazılmalı — kütüphane
    `from .constants import WSS_URL` yaptığı için constants'taki değişiklik
    işe yaramaz. Kütüphane güncellenirse bu fonksiyon zararsızdır.
    Referans: https://github.com/rany2/edge-tts/issues/490
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
        logger.warning("edge-tts yedek uç noktası devreye alındı (issue #490)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("edge-tts uç noktası düzeltmesi uygulanamadı: %s", exc)


def _cache_path(text: str, voice: str) -> Path:
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / f"{key}.mp3"


def _cleanup(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


async def _synthesize(text: str, voice: str) -> bytes:
    """edge-tts ile mp3 üretir; hata durumunda 503 fırlatır.

    Deneme sırası:
      1) kütüphanenin kendi uç noktası (DRM/Sec-MS-GEC ile)
      2) yalnızca 1. deneme hata verirse: yedek uç nokta
    Zaman aşımında yedek denenmez (uç nokta değiştirmek zaman aşımını çözmez).
    """
    try:
        import edge_tts  # noqa: PLC0415 — ağ istemcisi, ilk kullanımda
    except ImportError as exc:  # pragma: no cover
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
        # Benzersiz geçici ad: aynı metin iki kez eşzamanlı istenirse
        # iki istek aynı .tmp dosyasına yazıp bozuk mp3 üretmesin.
        tmp = target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            communicate = edge_tts.Communicate(text, voice)
            await asyncio.wait_for(communicate.save(str(tmp)), timeout=_TTS_TIMEOUT_S)
            os.replace(tmp, target)
            return target.read_bytes()
        except asyncio.TimeoutError as exc:
            last_error, timed_out = exc, True
        except Exception as exc:  # noqa: BLE001 — edge-tts resmî olmayan istemci, internet ister
            last_error = exc
        finally:
            _cleanup(tmp)

        if timed_out or use_patched or last_error is None:
            break

        status = getattr(last_error, "status", None)
        logger.warning(
            "TTS ilk deneme başarısız (%s); yedek uç nokta denenecek", last_error
        )
        if status == 429:
            await asyncio.sleep(_RETRY_SLEEP_S)

    if last_error is not None:
        if timed_out:
            logger.warning("TTS zaman aşımı (%.0f sn)", _TTS_TIMEOUT_S)
            raise HTTPException(
                status_code=503,
                detail="Seslendirme zaman aşımına uğradı. Panel tarayıcı sesine geçebilir.",
            ) from last_error
        logger.warning("TTS hatası: %s", last_error)
        raise HTTPException(
            status_code=503,
            detail="Seslendirme servisi şu an kullanılamıyor (edge-tts/internet). "
                   "Panel tarayıcı sesine geçebilir.",
        ) from last_error

    return target.read_bytes()


@router.post("/tts")
async def tts(payload: TtsRequest) -> Response:
    """Metni mp3 olarak döndürür (audio/mpeg)."""
    load_env()
    text = clean_text(payload.text)
    if not text:
        raise HTTPException(status_code=400, detail="Seslendirilecek metin boş.")

    voice = _voice()
    audio = await _synthesize(text, voice)
    logger.info("TTS: %d karakter -> %d bayt (ses: %s)", len(text), len(audio), voice)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )
