"""GET /shots/{name}: serves annotated desktop screenshots from backend/data/shots/."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.cua.annotate import SHOTS_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,96}\.png$")


@router.get("/shots/{name}")
async def get_shot(name: str) -> FileResponse:
    if not _VALID_NAME.fullmatch(name):
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı.")
    base = SHOTS_DIR.resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(status_code=404, detail="Ekran görüntüsü bulunamadı.")
    return FileResponse(path, media_type="image/png")
