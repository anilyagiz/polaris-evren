"""TEMPORARY SCAFFOLD — this file is owned by Person 3 (K3).

Replace it wholesale with K3's real main.py when it lands. This version exists
only so the Person 5 modules (STT/TTS + tools) can be exercised standalone;
/ws, the orchestrator and the browser bridge belong to K3.

Run from the repository root:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import sys

# The Windows console may be cp1252; keep Turkish log lines from crashing.
if sys.stderr.encoding and sys.stderr.encoding.lower().replace("_", "-") != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db import load_env

# Load .env before anything reads configuration (K3's llm.py included).
load_env()

# Importing the K5 tool modules registers them in the registry.
from backend.tools import memory as memory_tools  # noqa: E402,F401
from backend.tools import reminders as reminders_tools  # noqa: E402,F401
from backend.tools import search as search_tools  # noqa: E402,F401
from backend.voice.stt import router as stt_router  # noqa: E402
from backend.voice.tts import router as tts_router  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Assistant API", version="0.1")

# CORS: allow every origin, the chrome-extension:// scheme included.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Voice endpoints (K5).
app.include_router(stt_router)
app.include_router(tts_router)


@app.get("/health")
async def health() -> dict[str, bool]:
    """Liveness probe."""
    return {"ok": True}


# K3: the WebSocket /ws endpoint, orchestrator, bridge and /shots go here.
