"""GEÇİCİ İSKELET — bu dosyanın sahibi Kişi 3'tür.

Kişi 3'ün main.py dosyası geldiğinde bunu aynen yerine koyun.
Bu sürüm yalnızca Kişi 5 modüllerini (STT/TTS + araçlar) tek başına
test edebilmek için oluşturuldu; /ws, orchestrator ve bridge K3'tedir.

Çalıştırma (repo kökünden):
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import sys

# Windows konsolu cp1252 olabilir; Türkçe loglar çökmesin.
if sys.stderr.encoding and sys.stderr.encoding.lower().replace("_", "-") != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# K5 tool'ları import edilerek registry'ye kaydedilir.
from backend.tools import memory as memory_tools  # noqa: F401
from backend.tools import reminders as reminders_tools  # noqa: F401
from backend.tools import search as search_tools  # noqa: F401
from backend.voice.stt import router as stt_router
from backend.voice.tts import router as tts_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Assistant API", version="0.1")

# CORS: her origin'e izin ver (chrome-extension:// dahil).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ses uç noktaları (K5).
app.include_router(stt_router)
app.include_router(tts_router)


@app.get("/health")
async def health() -> dict[str, bool]:
    """Sağlık kontrolü."""
    return {"ok": True}


# K3: WebSocket /ws endpoint'i, orchestrator, bridge ve /shots buraya gelecek.
