"""Desktop mascot switch, so the panel's Ayarlar can show/hide the star.

The mascot lives in the tray process (tray/mascot.py), not in this backend, so
the switch is a shared file: ``backend/data/tray-settings.json`` →
``{"mascot": {"enabled": bool}}``. The tray polls that file and applies changes
within a few seconds.

    GET /settings/mascot  -> {"enabled": bool, "tray_running": bool}
    PUT /settings/mascot  {"enabled": bool} -> same shape

Same loopback + origin guard as ``/settings/llm``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import settings_api
from .config import BACKEND_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(settings_api._guard)])

#: The tray rewrites the file (window/mascot position) while it runs; a write
#: within this window means a tray is alive.
_TRAY_ALIVE_S = 24 * 3600


def settings_path() -> Path:
    override = os.environ.get("POLARIS_TRAY_SETTINGS")
    return Path(override) if override else BACKEND_DIR / "data" / "tray-settings.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _state() -> dict[str, Any]:
    data = _read()
    mascot = data.get("mascot") if isinstance(data.get("mascot"), dict) else {}
    path = settings_path()
    try:
        tray_running = (time.time() - path.stat().st_mtime) < _TRAY_ALIVE_S or "window" in data
    except OSError:
        tray_running = False
    return {"enabled": bool(mascot.get("enabled", True)), "tray_running": bool(tray_running)}


@router.get("/mascot")
async def get_mascot() -> dict[str, Any]:
    return _state()


@router.put("/mascot")
async def put_mascot(request: Request) -> dict[str, Any]:
    body = await settings_api._json_body(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="'enabled' true ya da false olmalı.")
    data = _read()
    mascot = data.get("mascot") if isinstance(data.get("mascot"), dict) else {}
    mascot["enabled"] = enabled
    data["mascot"] = mascot
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.info("maskot ayarı: %s", "açık" if enabled else "kapalı")
    return _state()
