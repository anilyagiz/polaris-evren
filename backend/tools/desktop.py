"""desktop.* tools (registered only when CUA_ENABLED=true).

CUA_ENABLED is read at import time, so main.py must load backend/.env before importing this module.
The panel's "Masaüstü kontrolü" toggle (backend/settings_desktop.py) calls
register_desktop_tools() / unregister_desktop_tools() live, without a restart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime

from backend.tools.registry import Tool, ToolContext, register, unregister
from backend.tools.registry import get as _registry_get

logger = logging.getLogger(__name__)

CLICK_SETTLE_S = 0.7
REF_NOT_FOUND = "ref bulunamadı, önce desktop.snapshot çağır"

# ref -> element from the most recent desktop.snapshot (shared by the single desktop session)
_elements: dict[int, dict] = {}
_shot_counter: defaultdict[str, int] = defaultdict(int)


def get_session():
    """Lazy: CUA/Pillow/OCR imports must never break backend startup."""
    from backend.cua.session import get_session as _get

    return _get()


def cua_enabled() -> bool:
    return os.getenv("CUA_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def element_name(ref: int) -> str | None:
    """Name of a ref from the last snapshot (for K3's risky-click confirm gate)."""
    el = _elements.get(int(ref))
    return el["name"] if el else None


def _bbox_center(bbox: list[int]) -> tuple[int, int]:
    x, y, w, h = bbox
    return x + w // 2, y + h // 2


def _parse_ref(args: dict) -> int | None:
    try:
        return int(args.get("ref"))
    except (TypeError, ValueError):
        return None


async def _emit_snapshot_trace(ctx: ToolContext, snapshot: dict, latency_ms: int) -> None:
    from backend.trace import make_step

    step = make_step(
        "executor",
        f"Masaüstü ekran görüntüsü alındı ({len(snapshot['elements'])} öğe)",
        "Masaüstünde ne olduğunu görmek için ekran metne çevrildi; numaralar tıklanabilir öğeleri gösterir.",
        evidence=[f"shot:{snapshot['shot']}"],
        tool={"name": "desktop.snapshot", "args": {}},
        latency_ms=latency_ms,
    )
    try:
        await ctx.emit_trace(step)
    except Exception:
        logger.exception("emit_trace failed for desktop.snapshot")


async def _snapshot(args: dict, ctx: ToolContext) -> dict:
    started = time.perf_counter()
    try:
        from backend.cua.perceive import perceive

        session = get_session()
        png = await session.screenshot()
        _shot_counter[ctx.request_id] += 1
        known = getattr(session, "known_elements", None)  # fake desktop: skip OCR
        snapshot = await perceive(
            png, ctx.request_id, _shot_counter[ctx.request_id], boxes=known() if known else None
        )
    except ImportError as exc:
        logger.warning("desktop.snapshot: eksik paket: %s", exc)
        return {
            "ok": False,
            "error": "Masaüstü özelliği için gerekli paketler kurulu değil "
            f"({exc.name or exc}). docs/cua-setup.md dosyasına bak ya da DESKTOP_FAKE=1 kullan.",
        }
    except Exception as exc:
        logger.exception("desktop.snapshot failed")
        return {"ok": False, "error": f"Masaüstü görüntüsü alınamadı: {exc}"}
    _elements.clear()
    _elements.update({el["ref"]: el for el in snapshot["elements"]})
    await _emit_snapshot_trace(ctx, snapshot, int((time.perf_counter() - started) * 1000))
    return {"ok": True, "data": snapshot}  # snapshot includes "shot": "/shots/{request_id}_{n}.png"


async def _click(args: dict, ctx: ToolContext) -> dict:
    ref = _parse_ref(args)
    el = _elements.get(ref) if ref is not None else None
    if el is None:
        return {"ok": False, "error": REF_NOT_FOUND}
    x, y = _bbox_center(el["bbox"])
    try:
        await get_session().click(x, y)
        await asyncio.sleep(CLICK_SETTLE_S)
    except Exception as exc:
        logger.exception("desktop.click failed")
        return {"ok": False, "error": f"Tıklanamadı: {exc}"}
    return {"ok": True}


async def _type(args: dict, ctx: ToolContext) -> dict:
    text = str(args.get("text") or "")
    if not text:
        return {"ok": False, "error": "Yazılacak metin boş olamaz."}
    try:
        await get_session().type_text(text)
    except Exception as exc:
        logger.exception("desktop.type failed")
        return {"ok": False, "error": f"Yazılamadı: {exc}"}
    return {"ok": True}


async def _key(args: dict, ctx: ToolContext) -> dict:
    combo = str(args.get("combo") or "").strip()
    if not combo:
        return {"ok": False, "error": "Tuş kombinasyonu boş olamaz, ör. 'ctrl+l'."}
    try:
        await get_session().press(combo)
    except Exception as exc:
        logger.exception("desktop.key failed")
        return {"ok": False, "error": f"Tuşa basılamadı: {exc}"}
    return {"ok": True}


async def _scroll(args: dict, ctx: ToolContext) -> dict:
    direction = str(args.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        return {"ok": False, "error": "direction 'up' ya da 'down' olmalı."}
    try:
        await get_session().scroll(direction)
    except Exception as exc:
        logger.exception("desktop.scroll failed")
        return {"ok": False, "error": f"Kaydırılamadı: {exc}"}
    return {"ok": True}


TOOLS = [
    Tool(
        name="desktop.snapshot",
        description="Sanal masaüstünün ekran görüntüsünü alır ve ekrandaki metinleri numaralı öğe listesine çevirir. "
        "desktop.click kullanmadan önce mutlaka çağır; ekran değiştikten sonra tekrar çağır.",
        parameters={"type": "object", "properties": {}},
        risk="safe",
        handler=_snapshot,
    ),
    Tool(
        name="desktop.click",
        description="Son desktop.snapshot listesindeki numaralı öğenin ortasına tıklar.",
        parameters={
            "type": "object",
            "properties": {"ref": {"type": "integer", "description": "Snapshot'taki öğe numarası."}},
            "required": ["ref"],
        },
        risk="safe",
        handler=_click,
    ),
    Tool(
        name="desktop.type",
        description="Sanal masaüstünde odaktaki alana metin yazar.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Yazılacak metin."}},
            "required": ["text"],
        },
        risk="safe",
        handler=_type,
    ),
    Tool(
        name="desktop.key",
        description="Tuş ya da tuş kombinasyonuna basar, ör. 'enter', 'ctrl+l', 'ctrl+s'.",
        parameters={
            "type": "object",
            "properties": {"combo": {"type": "string", "description": "Tuşlar '+' ile ayrılır, ör. 'ctrl+l'."}},
            "required": ["combo"],
        },
        risk="safe",
        handler=_key,
    ),
    Tool(
        name="desktop.scroll",
        description="Sanal masaüstünde ekranın ortasında yukarı ya da aşağı kaydırır.",
        parameters={
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": ["up", "down"]}},
            "required": ["direction"],
        },
        risk="safe",
        handler=_scroll,
    ),
]

_registered = False


def register_desktop_tools() -> bool:
    """Register desktop.* tools; clean no-op when CUA_ENABLED is false. Idempotent."""
    global _registered
    if not cua_enabled():
        logger.info("CUA disabled (CUA_ENABLED=false): desktop tools not registered")
        return False
    if not _registered or any(_registry_get(t.name) is not t for t in TOOLS):
        for tool in TOOLS:
            register(tool)
        _registered = True
        logger.info("CUA enabled: %d desktop tools registered", len(TOOLS))
    return True


def unregister_desktop_tools() -> None:
    """Remove desktop.* tools from the registry (live disable). Idempotent."""
    global _registered
    for tool in TOOLS:
        unregister(tool.name)
    _registered = False
    _elements.clear()
    logger.info("CUA disabled: desktop tools unregistered")


register_desktop_tools()
