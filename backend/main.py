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
import importlib.util
import logging
import re
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# K5's hand-rolled .env loader runs before anything reads configuration (their
# modules call it too; it is idempotent and never overwrites real env vars).
try:  # pragma: no cover - trivial guard
    from .db import load_env

    load_env()
except Exception:  # backend/db.py not merged yet
    pass

from .config import APP_VERSION, config_warnings, ensure_data_dir, get_settings  # noqa: E402
from .logging_setup import setup_logging  # noqa: E402
from .trace import MAX_HISTORY_LIMIT, TraceStore  # noqa: E402

setup_logging()
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
        "Polaris %s hazır — model %s, CUA %s",
        APP_VERSION,
        settings.llm_model,
        "açık" if settings.cua_enabled else "kapalı",
    )
    for warning in config_warnings(settings):
        logger.warning("Yapılandırma: %s", warning)
    yield


app = FastAPI(title="Polaris Backend", version=APP_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Rate limit for the voice endpoints (CPU-heavy STT, network-bound TTS).
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window_s` per key."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = int(limit)
        self.window_s = float(window_s)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Record one hit; returns seconds to wait when over the limit, else None."""

        if self.limit <= 0:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > 1024:  # forget idle clients
                for stale in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_s]:
                    self._hits.pop(stale, None)
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] >= self.window_s:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(0.0, self.window_s - (now - bucket[0]))
            bucket.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


RATE_LIMITED_PATHS: frozenset[str] = frozenset({"/stt", "/tts"})
voice_limiter = RateLimiter(settings.voice_rate_limit_per_min, 60.0)


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def voice_rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path.rstrip("/") or "/"
    if request.method != "OPTIONS" and path in RATE_LIMITED_PATHS:
        host = _client_host(request)
        wait_s = voice_limiter.hit(f"{host} {path}")
        if wait_s is not None:
            retry = max(1, int(wait_s + 0.999))
            logger.warning("Hız sınırı aşıldı: %s %s", host, path)
            return JSONResponse(
                {
                    "detail": (
                        f"Çok fazla istek: dakikada en fazla {voice_limiter.limit} ses isteği "
                        f"gönderilebilir. {retry} sn sonra tekrar deneyin."
                    )
                },
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
    return await call_next(request)


# CORS: only our extension and localhost pages.  Added after the rate-limit
# middleware so it wraps it (429 answers carry CORS headers too).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_register_tools()
_include_optional_routers(app)


def _stt_ready() -> bool:
    """Is the Whisper model already loaded?  Never triggers a (slow) load."""

    module = sys.modules.get(f"{__package__}.voice.stt")
    return getattr(module, "_MODEL", None) is not None


def _tts_available() -> bool:
    try:
        return importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ValueError):
        return False


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe (the panel and the demo script both use it)."""

    return {
        "ok": True,
        "service": "polaris",
        "version": APP_VERSION,
        "llm_model": settings.llm_model,
        "cua_enabled": settings.cua_enabled,
        "llm_configured": settings.llm_configured,
        "brave_configured": settings.brave_configured,
        "stt_ready": _stt_ready(),
        "tts_available": _tts_available(),
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


def is_loopback(host: str | None) -> bool:
    """127.0.0.0/8, ::1 and IPv4-mapped loopback; everything else is remote."""

    if not host:
        return False
    host = host.strip().lower()
    return (
        host in {"::1", "localhost"}
        or host.startswith("127.")
        or host.startswith("::ffff:127.")
    )


@app.get("/traces/{request_id}")
async def traces(request: Request, request_id: str, limit: int = 200) -> JSONResponse:
    """Debug helper: replay one request's explainability timeline (loopback only)."""

    host = _client_host(request)
    if not is_loopback(host):
        logger.warning("/traces isteği reddedildi (istemci=%s)", host)
        return JSONResponse(
            {"detail": "İz kayıtlarına yalnızca bu bilgisayardan (localhost) erişilebilir."},
            status_code=403,
        )
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    rows = await store.history(request_id, limit=limit)
    return JSONResponse({"request_id": request_id, "steps": rows})


def run() -> None:  # pragma: no cover - manual entry point
    from .__main__ import main

    main([])


if __name__ == "__main__":  # pragma: no cover
    run()
