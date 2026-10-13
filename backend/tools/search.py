"""web.search — Brave Search API araması (Kişi 5 sahibi).

Sözleşme:
    tool adı : web.search
    argüman  : {query: str, count?: int}
    dönüş    : {ok, results: [{title, url, snippet}]}
    risk     : safe

Hatalar exception yerine {"ok": False, "error": "…"} ile döner.
"""
from __future__ import annotations

import logging
import os
import re
from html import unescape
from typing import Any

import httpx

from backend.db import load_env
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT_S = 10.0
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Snippet'teki HTML etiketlerini temizler."""
    plain = _TAG_RE.sub(" ", text or "")
    plain = unescape(plain)
    return _SPACE_RE.sub(" ", plain).strip()


async def _search(args: dict, ctx: ToolContext) -> dict:
    """Brave'de web araması yapar."""
    load_env()
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Arama sorgusu boş."}

    api_key = (os.environ.get("BRAVE_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "error": "BRAVE_API_KEY ayarlanmamış (backend/.env)."}

    try:
        count = int(args.get("count") or 5)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, 10))

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers) as client:
            resp = await client.get(BRAVE_URL, params={"q": query, "count": count})
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Brave arama zaman aşımına uğradı ({_TIMEOUT_S:.0f} sn)."}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Ağ hatası: {exc}"}

    if resp.status_code == 429:
        return {
            "ok": False,
            "error": "Brave API istek limiti aşıldı (429); kısa bir süre bekleyip tekrar deneyin.",
        }
    if resp.status_code in (401, 403):
        return {"ok": False, "error": "BRAVE_API_KEY geçersiz veya yetkisiz (401/403)."}
    if resp.status_code != 200:
        body = resp.text[:200]
        return {"ok": False, "error": f"Brave API hatası ({resp.status_code}): {body}"}

    try:
        data: Any = resp.json()
    except ValueError:
        return {"ok": False, "error": "Brave API geçersiz JSON döndürdü."}

    raw_results = ((data or {}).get("web") or {}).get("results") or []
    results = []
    for item in raw_results[:count]:
        url = str(item.get("url") or "")
        title = _clean(str(item.get("title") or "")) or url
        snippet = _clean(str(item.get("description") or ""))
        if not url:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})

    logger.info("web.search: %r -> %d sonuç", query, len(results))
    return {"ok": True, "results": results}


search_tool = Tool(
    name="web.search",
    description="Brave'de web araması yapar; başlık, URL ve kısa özet döndürür. "
                "Güncel bilgi, fiyat veya kaynak gerektiğinde kullan.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Türkçe veya İngilizce arama sorgusu",
            },
            "count": {
                "type": "integer",
                "description": "Sonuç sayısı (1-10), varsayılan 5",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    },
    risk="safe",
    handler=_search,
)

register(search_tool)
