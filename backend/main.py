"""FastAPI application entry point.  Owner: K3.

This replaces the K5 scaffold (see docs/HANDOFF-K3.md §1).  It keeps what K5
needs — ``load_env()`` before any configuration is read, the K5 tool modules
imported so they register themselves, and the STT/TTS routers — and adds the
parts that belong to K3: ``/ws`` (orchestrator), the browser bridge, the
explainability traces and the desktop shots router.

Runs even while other owners' modules (or their dependencies) are missing:
every optional import is guarded and logged.
"""

from __future__ import annotations

import importlib
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# K5's hand-rolled .env loader runs before anything reads configuration (their
# modules call it too; it is idempotent and never overwrites real env vars).
try:  # pragma: no cover - trivial guard
    from .db import load_env

    load_env()
except Exception:  # backend/db.py not merged yet
    pass

from .config import ensure_data_dir, get_settings  # noqa: E402
from .trace import TraceStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backend.main")

settings = get_settings()
store = TraceStore()


# ---------------------------------------------------------------------------
# Tool modules.  Importing them is what registers their tools in the registry
# (backend/tools/__init__.py stays empty on purpose: no double registration).
# ---------------------------------------------------------------------------
TOOL_MODULES: tuple[str, ...] = (
    ".tools.browser",  # K3
    ".tools.search",  # K5
    ".tools.memory",  # K5
    ".tools.reminders",  # K5
)

# Routers owned by other people.  Missing files are not fatal.
OPTIONAL_ROUTERS: tuple[tuple[str, str], ...] = (
    (".voice.stt", "router"),  # K5 -> POST /stt
    (".voice.tts", "router"),  # K5 -> POST /tts
    (".cua.shots_router", "router"),  # K4 -> GET /shots/{name}
)


def _import_module(module_path: str) -> Any | None:
    try:
        return importlib.import_module(module_path, package=__package__)
    except Exception as exc:  # ModuleNotFoundError, ImportError, syntax error...
        logger.info("Opsiyonel modül yüklenemedi %s: %s", module_path, exc)
        return None


def _register_tools() -> None:
    from .tools import registry

    for module_path in TOOL_MODULES:
        _import_module(module_path)
    if settings.cua_enabled:  # K4 desktop.* tools: optional, never fatal
        _import_module(".tools.desktop")
    names = sorted(tool.name for tool in registry.all_tools())
    logger.info("Kayıtlı araçlar (%d): %s", len(names), ", ".join(names) or "-")


def _include_optional_routers(target: FastAPI) -> None:
    for module_path, attr in OPTIONAL_ROUTERS:
        if module_path == ".cua.shots_router" and not settings.cua_enabled:
            continue
        module = _import_module(module_path)
        if module is None:
            continue
        router = getattr(module, attr, None)
        if router is None:
            logger.warning("%s içinde %s yok, atlanıyor", module_path, attr)
            continue
        target.include_router(router)
        logger.info("Router eklendi: %s.%s", module_path, attr)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_data_dir()
    await store.init()
    logger.info(
        "Backend hazır — port %s, model %s, CUA %s",
        settings.port,
        settings.llm_model,
        "açık" if settings.cua_enabled else "kapalı",
    )
    if not settings.llm_configured:
        logger.warning("DEEPSEEK_API_KEY boş: LLM gerektiren istekler hata dönecek.")
    yield


app = FastAPI(title="Personal Assistant Backend", version="0.1", lifespan=lifespan)

# CORS: only our extension and localhost pages.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_register_tools()
_include_optional_routers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe (the K1 panel and the demo script both use it)."""

    return {
        "ok": True,
        "llm_model": settings.llm_model,
        "cua_enabled": settings.cua_enabled,
        "llm_configured": settings.llm_configured,
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
async def _echo_connection(websocket: WebSocket) -> None:
    """Fallback used when the orchestrator cannot be imported."""

    await websocket.accept()
    await websocket.send_json(
        {"type": "error", "request_id": None, "message": "orchestrator yüklenemedi"}
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Echo bağlantısı kapandı: %s", exc)


def _resolve_ws_handler() -> Callable[[WebSocket], Any]:
    try:
        from .orchestrator import serve_connection
    except Exception as exc:
        logger.error("orchestrator içe aktarılamadı (%s); echo fallback aktif", exc)
        return _echo_connection
    return serve_connection


_ALLOWED_WS_ORIGIN = re.compile(
    r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?)$"
)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    # Browsers always send Origin on WebSocket handshakes; only our extension and
    # localhost pages may drive the assistant. Non-browser clients (CLI, tests) send none.
    origin = websocket.headers.get("origin")
    if origin and not _ALLOWED_WS_ORIGIN.match(origin):
        logger.warning("WS bağlantısı reddedildi (origin=%s)", origin)
        await websocket.close(code=1008)
        return
    handler = _resolve_ws_handler()
    try:
        await handler(websocket)
    except WebSocketDisconnect:
        logger.info("İstemci bağlantıyı kapattı")
    except Exception as exc:  # never take the process down for one socket
        logger.exception("WS handler beklenmeyen hata: %s", exc)
        try:
            await websocket.send_json({"type": "error", "request_id": None, "message": str(exc)})
        except Exception:
            pass


@app.get("/traces/{request_id}")
async def traces(request_id: str, limit: int = 200) -> JSONResponse:
    """Debug helper: replay one request's explainability timeline."""

    rows = await store.history(request_id, limit=limit)
    return JSONResponse({"request_id": request_id, "steps": rows})


def run() -> None:  # pragma: no cover - manual entry point
    import uvicorn

    uvicorn.run("backend.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":  # pragma: no cover
    run()
