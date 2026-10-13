"""Serve the built side panel at ``/app/`` for the Polaris desktop window.

The tray's native window (pywebview / WebView2) loads ``http://127.0.0.1:<port>/app/``,
so the panel talks to the backend same-origin: no settings, no port discovery and
the CORS / WebSocket origin checks already allow ``http://127.0.0.1:<port>``.

Vite builds the panel with absolute asset URLs (``/assets/...``); the index page is
rewritten to ``/app/assets/...`` when served.  Chunk-to-chunk imports inside
``assets/`` are relative, so they resolve under ``/app/assets/`` unchanged.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import PROJECT_DIR

logger = logging.getLogger(__name__)

APP_PREFIX = "/app"
INDEX_REL = "src/panel/index.html"
NOT_BUILT = (
    "Polaris paneli derlenmemiş (extension\\dist yok). "
    "Derlemek için: scripts\\build-extension.ps1"
)

# Windows' registry can map .js to text/plain; browsers refuse such module scripts.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")


def panel_dist(root: Path | None = None) -> Path:
    """Release ships ``extension-dist/``; a dev checkout builds ``extension/dist``."""

    base = root or PROJECT_DIR
    packaged = base / "extension-dist"
    if (packaged / INDEX_REL).is_file():
        return packaged
    return base / "extension" / "dist"


def rewrite_index(html: str, prefix: str = APP_PREFIX) -> str:
    """Point the built page's absolute asset URLs at the ``/app`` mount."""

    for attr in ('src="/', 'href="/'):
        html = html.replace(f'{attr}assets/', f'{attr}{prefix.strip("/")}/assets/')
    return html


def _safe_file(dist: Path, rel: str) -> Path | None:
    try:
        target = (dist / rel).resolve()
        base = dist.resolve()
    except (OSError, ValueError):
        return None
    if target != base and base not in target.parents:
        return None
    return target if target.is_file() else None


def register(app: FastAPI, dist: Path | None = None) -> None:
    """Add ``/app``, ``/app/`` and ``/app/{path}`` to ``app``."""

    def current_dist() -> Path:
        return dist if dist is not None else panel_dist()

    def index() -> Response:
        page = current_dist() / INDEX_REL
        try:
            html = page.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Panel index bulunamadı: %s", page)
            return JSONResponse({"detail": NOT_BUILT}, status_code=404)
        return HTMLResponse(rewrite_index(html), headers={"Cache-Control": "no-cache"})

    @app.get(APP_PREFIX, include_in_schema=False)
    async def app_root_redirect() -> Response:
        return RedirectResponse(f"{APP_PREFIX}/", status_code=307)

    @app.get(f"{APP_PREFIX}/", include_in_schema=False)
    async def app_index() -> Response:
        return index()

    @app.get(APP_PREFIX + "/{path:path}", include_in_schema=False)
    async def app_file(path: str) -> Response:
        if path in ("", INDEX_REL, "index.html"):
            return index()
        target = _safe_file(current_dist(), path)
        if target is None:
            return JSONResponse({"detail": "Bulunamadı"}, status_code=404)
        media, _ = mimetypes.guess_type(target.name)
        # Hashed asset names never change content; the index itself is no-cache.
        cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "no-cache"
        return FileResponse(target, media_type=media, headers={"Cache-Control": cache})
