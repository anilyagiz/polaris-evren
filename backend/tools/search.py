"""web.search — Brave Search API backed tool (owner: Person 5 / K5).

Contract (ORTAK BAĞLAM §7):
    name       : web.search
    arguments  : {query: str, count?: int}
    returns    : {ok: true, results: [{title, url, snippet}]}
    risk       : safe

Errors are returned as ``{"ok": False, "error": "..."}`` instead of raising.
"""
from __future__ import annotations

import html
import logging
import os
import re

import httpx

from backend import db
from backend.tools.registry import Tool, ToolContext

logger = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_COUNT = 5
MAX_COUNT = 20  # Brave API limit
_TIMEOUT_S = 10.0

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Strip HTML tags and decode entities from a snippet."""
    return _WHITESPACE.sub(" ", html.unescape(_TAG.sub("", text or ""))).strip()


async def web_search(query: str, count: int = DEFAULT_COUNT) -> dict:
    """Run a Brave web search. Never raises; always returns a dict with ``ok``."""
    db.load_env()
    api_key = os.getenv("BRAVE_API_KEY", "").strip()
    if not api_key:
        if os.getenv("WEB_SEARCH_FALLBACK", "duckduckgo").strip().lower() in ("off", "none", "0", "false"):
            return {"ok": False, "error": "Web araması yapılandırılmamış: BRAVE_API_KEY eksik."}
        return await _duckduckgo_search(query, count)

    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": query[:400], "count": count}  # Brave rejects queries over 400 chars
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.get(BRAVE_URL, headers=headers, params=params)
    except httpx.TimeoutException:
        logger.warning("Brave search timed out for %r", query)
        return {"ok": False, "error": "Arama zaman aşımına uğradı, lütfen tekrar deneyin."}
    except httpx.HTTPError as exc:
        logger.warning("Brave search network error: %s", exc)
        return {"ok": False, "error": "Arama servisine ulaşılamadı. İnternet bağlantısını kontrol edin."}

    if response.status_code == 429:
        return {"ok": False, "error": "Arama limiti aşıldı, biraz sonra tekrar deneyin."}
    # Brave reports an invalid key as 422 SUBSCRIPTION_TOKEN_INVALID rather than 401.
    if response.status_code in (401, 403) or "SUBSCRIPTION_TOKEN_INVALID" in response.text:
        return {"ok": False, "error": "Brave API anahtarı geçersiz ya da yetkisiz."}
    if response.status_code >= 400:
        logger.warning("Brave search HTTP %s: %s", response.status_code, response.text[:300])
        return {"ok": False, "error": f"Arama servisi hata döndürdü (HTTP {response.status_code})."}

    try:
        payload = response.json()
        items = (payload.get("web") or {}).get("results") or []
    except (ValueError, AttributeError):
        return {"ok": False, "error": "Arama servisinden geçersiz yanıt alındı."}

    results = [
        {
            "title": strip_html(item.get("title", "")) or item.get("url", ""),
            "url": item.get("url", ""),
            "snippet": strip_html(item.get("description", "")),
        }
        for item in items[:count]
        if isinstance(item, dict) and item.get("url")
    ]
    logger.info("web.search: %r -> %d results", query, len(results))
    return {"ok": True, "results": results}


DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_RESULT = re.compile(
    r'class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.S,
)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def _ddg_target(url: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<encoded>; unwrap them."""
    from urllib.parse import parse_qs, unquote, urlparse

    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) or url
    return url


async def _duckduckgo_search(query: str, count: int) -> dict:
    """Keyless fallback used when BRAVE_API_KEY is not set."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            response = await client.post(DDG_URL, data={"q": query[:400]}, headers={"User-Agent": _UA})
    except httpx.TimeoutException:
        return {"ok": False, "error": "Arama zaman aşımına uğradı, lütfen tekrar deneyin."}
    except httpx.HTTPError as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return {"ok": False, "error": "Arama servisine ulaşılamadı. İnternet bağlantısını kontrol edin."}
    if response.status_code != 200:
        return {"ok": False, "error": f"Arama servisi hata döndürdü (HTTP {response.status_code})."}
    results = []
    for match in _DDG_RESULT.finditer(response.text):
        url = _ddg_target(match.group("url"))
        if not url.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in url:
            continue  # sponsored/ad links
        results.append(
            {"title": strip_html(match.group("title")), "url": url, "snippet": strip_html(match.group("snippet"))}
        )
        if len(results) >= count:
            break
    if not results:
        return {"ok": False, "error": "Arama sonuç döndürmedi; sorguyu değiştirip tekrar deneyin."}
    return {"ok": True, "results": results, "provider": "duckduckgo"}


async def _handle_search(args: dict, ctx: ToolContext) -> dict:
    """Tool handler: validate arguments and delegate to web_search."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Arama sorgusu boş olamaz."}
    try:
        count = int(args.get("count") or DEFAULT_COUNT)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    return await web_search(query, max(1, min(count, MAX_COUNT)))


db.register_once(
    Tool(
        name="web.search",
        description="İnternette arama yapar (Brave Search API, anahtar yoksa DuckDuckGo); her sonuç için başlık, URL ve kısa özet döndürür. "
        "Güncel bilgi, ürün karşılaştırma ve kaynak gereken sorular için kullan.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Arama sorgusu."},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_COUNT,
                    "default": DEFAULT_COUNT,
                    "description": "Döndürülecek sonuç sayısı (varsayılan 5).",
                },
            },
            "required": ["query"],
        },
        risk="safe",
        handler=_handle_search,
    )
)
