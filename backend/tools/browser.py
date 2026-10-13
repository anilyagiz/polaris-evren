"""`browser.*` tools: thin wrappers around :class:`BrowserBridge`.

Owner: K3.  Frozen contract: ORTAK BAĞLAM section 7.  These handlers hold no
browser logic at all — every call is forwarded to the extension over the
WebSocket bridge and the answer is normalised to the documented return value.
A failing bridge (timeout, closed socket, dead extension) never raises: it
becomes ``{"ok": False, "error": ...}``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import get_settings
from .registry import Tool, ToolContext, get, is_truthy, register

logger = logging.getLogger(__name__)

#: Longest text handed to the LLM by `browser.read`.
MAX_READ_CHARS = 4000

# ---------------------------------------------------------------------------
# Bridge plumbing
# ---------------------------------------------------------------------------
async def _bridge_call(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """One bridge round-trip, normalised to ``{'ok', 'data', 'error'}``.

    Never raises: a broken bridge is reported as a tool-level failure so the
    executor can explain it and the request keeps going.
    """

    timeout = get_settings().tool_timeout_s
    try:
        raw = await ctx.bridge.call(name, args, timeout=timeout)
    except Exception as exc:  # bridge must never take the request down
        logger.warning("%s köprü çağrısı başarısız: %s", name, exc)
        return {"ok": False, "data": None, "error": f"tarayıcı köprüsü hatası: {exc}"}

    if not isinstance(raw, dict):
        logger.warning("%s köprüden geçersiz yanıt: %r", name, raw)
        return {"ok": False, "data": None, "error": "tarayıcı köprüsü geçersiz yanıt döndürdü"}

    if raw.get("ok"):
        return {"ok": True, "data": raw.get("data"), "error": None}
    return {
        "ok": False,
        "data": raw.get("data"),
        "error": str(raw.get("error") or "bilinmeyen tarayıcı hatası"),
    }


def _failure(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": result.get("error") or "bilinmeyen tarayıcı hatası"}


def _trim(text: str, limit: int = MAX_READ_CHARS) -> str:
    """Cut long page text for the LLM, marking the cut with an ellipsis."""

    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _snapshot(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.snapshot", args, ctx)
    if not result["ok"]:
        return _failure(result)
    data = result["data"]
    if not isinstance(data, dict):
        logger.warning("browser.snapshot sözlük olmayan veri döndürdü: %r", type(data))
        return {"ok": False, "error": "tarayıcı snapshot verisi beklenen biçimde değil"}
    # §7 handler contract: always the {"ok", "data", "error"} envelope, never the
    # bare snapshot dict (the orchestrator used to rescue that shape by guessing).
    return {"ok": True, "data": data}


async def _click(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.click", args, ctx)
    if not result["ok"]:
        return _failure(result)
    return {"ok": True}


async def _type(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    # submit=true is gated by registry.needs_confirm before this handler runs.
    # The flag is normalised to a real boolean so the gate and the extension can
    # never disagree about whether the form is submitted (e.g. submit="true").
    payload = dict(args)
    if "submit" in payload:
        payload["submit"] = is_truthy(payload.get("submit"))
    result = await _bridge_call("browser.type", payload, ctx)
    if not result["ok"]:
        return _failure(result)
    return {"ok": True}


async def _scroll(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.scroll", args, ctx)
    if not result["ok"]:
        return _failure(result)
    return {"ok": True}


async def _navigate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.navigate", args, ctx)
    if not result["ok"]:
        return _failure(result)
    data = result["data"] if isinstance(result["data"], dict) else {}
    return {
        "ok": True,
        "data": {
            "url": str(data.get("url") or args.get("url") or ""),
            "title": str(data.get("title") or ""),
        },
    }


#: Schemes `browser.open` may hand to the extension.  A bare host
#: ("tr.wikipedia.org") is fine too: the extension prefixes https://.
_OPEN_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):(?!\d)", re.IGNORECASE)


def _open_url_error(raw: Any) -> str | None:
    """Same allow-list as `browser.navigate` (http/https only), checked early.

    The extension enforces it again (`normalizeNavigateUrl`); this copy only
    saves a bridge round-trip and gives the model a clear Turkish reason.
    """

    if not isinstance(raw, str) or not raw.strip():
        return "url eksik, http(s) ile başlayan bir adres ver"
    match = _OPEN_SCHEME.match(raw.strip())
    if match and match.group(1).lower() not in ("http", "https"):
        return f'"{match.group(1).lower()}:" adresleri açılamaz, yalnızca http/https'
    return None


async def _open(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    error = _open_url_error(args.get("url"))
    if error is not None:
        return {"ok": False, "error": error}
    payload: dict[str, Any] = {"url": str(args["url"]).strip()}
    # Default is a NEW tab; only an explicit false keeps the current tab.
    payload["new_tab"] = True if args.get("new_tab") is None else is_truthy(args.get("new_tab"))
    result = await _bridge_call("browser.open", payload, ctx)
    if not result["ok"]:
        return _failure(result)
    data = result["data"] if isinstance(result["data"], dict) else {}
    return {
        "ok": True,
        "data": {
            "url": str(data.get("url") or payload["url"]),
            "title": str(data.get("title") or ""),
            "new_tab": bool(data.get("new_tab", payload["new_tab"])),
        },
    }


async def _read(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.read", args, ctx)
    if not result["ok"]:
        return _failure(result)
    data = result["data"]
    text = data.get("text") if isinstance(data, dict) else data
    payload: dict[str, Any] = {"text": _trim(text if isinstance(text, str) else "")}
    if isinstance(data, dict):
        for key in ("url", "title"):
            if data.get(key):
                payload[key] = str(data[key])
    # §7: bare {"text": ...} used to be rejected by the orchestrator, which turned
    # every successful page read into a tool error.
    return {"ok": True, "data": payload}


async def _highlight(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = await _bridge_call("browser.highlight", args, ctx)
    if not result["ok"]:
        return _failure(result)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Schemas and registration
# ---------------------------------------------------------------------------
def _object_schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


_REF = {"type": "integer", "description": "Snapshot'taki öğe referans numarası (ref)."}


def _build_tools() -> list[Tool]:
    """Fresh Tool objects for the eight `browser.*` tools."""

    return [
        Tool(
            name="browser.snapshot",
            description=(
                "Aktif sekmenin erişilebilirlik ağacını getirir: tıklanabilir öğeler, "
                "ref numaraları, sayfa başlığı ve kısa metin özeti. Her tarayıcı görevine "
                "bununla başla; bir öğeye tıklamadan veya yazmadan önce ref numaralarını "
                "güncel snapshot'tan al."
            ),
            parameters=_object_schema({}),
            risk="safe",
            handler=_snapshot,
        ),
        Tool(
            name="browser.click",
            description=(
                "Snapshot'ta verilen ref numarasındaki öğeye tıklar. Ref güncel snapshot'tan "
                "gelmiyorsa önce browser.snapshot çağır; gönder/satın al/sil gibi riskli "
                "etiketlerde kullanıcıdan onay istenir."
            ),
            parameters=_object_schema({"ref": _REF}, ["ref"]),
            risk="safe",
            handler=_click,
        ),
        Tool(
            name="browser.type",
            description=(
                "Verilen ref numarasındaki giriş alanına metin yazar. Formları doldurmak için "
                "kullan; submit=true verirsen metin gönderilir ve bu işlem kullanıcı onayına "
                "tabidir."
            ),
            parameters=_object_schema(
                {
                    "ref": _REF,
                    "text": {"type": "string", "description": "Yazılacak metin."},
                    "submit": {
                        "type": "boolean",
                        "description": "true ise yazdıktan sonra formu gönderir (onay gerektirir).",
                    },
                },
                ["ref", "text"],
            ),
            risk="safe",
            handler=_type,
        ),
        Tool(
            name="browser.scroll",
            description=(
                "Aktif sekmeyi bir ekran yukarı veya aşağı kaydırır. Aradığın öğe snapshot'ta "
                "görünmüyorsa sayfayı kaydırıp yeniden snapshot al."
            ),
            parameters=_object_schema(
                {"direction": {"type": "string", "enum": ["up", "down"], "description": "Kaydırma yönü."}},
                ["direction"],
            ),
            risk="safe",
            handler=_scroll,
        ),
        Tool(
            name="browser.navigate",
            description=(
                "Aktif sekmeyi verilen adrese yönlendirir ve açılan sayfanın başlığını döndürür. "
                "Yeni bir sayfaya gitmek veya elindeki bir URL'yi açmak için kullan."
            ),
            parameters=_object_schema(
                {"url": {"type": "string", "description": "Açılacak tam adres (https://...)."}},
                ["url"],
            ),
            risk="safe",
            handler=_navigate,
        ),
        Tool(
            name="browser.open",
            description=(
                "Verilen adresi YENİ bir sekmede açar, sekmeyi öne getirir ve yüklenmesini "
                "bekler; sonraki tüm browser.* çağrıları bu sekmede çalışır. Kullanıcı bir "
                "site istiyorsa ('Vikipedi'yi aç') veya aktif sekme boşsa/yeni sekmeyse bunu "
                "kullan. new_tab=false verirsen mevcut görev sekmesinde açar."
            ),
            parameters=_object_schema(
                {
                    "url": {"type": "string", "description": "Açılacak tam adres (https://...)."},
                    "new_tab": {
                        "type": "boolean",
                        "description": "Varsayılan true: yeni sekmede aç. false: mevcut sekmede aç.",
                    },
                },
                ["url"],
            ),
            risk="safe",
            handler=_open,
        ),
        Tool(
            name="browser.read",
            description=(
                "Sayfanın metnini okur; ref verilirse yalnızca o öğenin metnini getirir. İçeriği "
                "özetlemek, bir değeri doğrulamak veya cevaba kanıt toplamak için kullan "
                "(dönen metin en fazla 4000 karakterdir)."
            ),
            parameters=_object_schema(
                {"ref": {**_REF, "description": "İsteğe bağlı: sadece bu öğenin metnini okur."}}
            ),
            risk="safe",
            handler=_read,
        ),
        Tool(
            name="browser.highlight",
            description=(
                "Snapshot'taki bir öğeyi kullanıcıya görsel olarak vurgular. Kullanıcıya hangi "
                "öğeden bahsettiğini göstermek için kullan; sayfayı değiştirmez."
            ),
            parameters=_object_schema({"ref": _REF}, ["ref"]),
            risk="safe",
            handler=_highlight,
        ),
    ]


def register_browser_tools() -> list[Tool]:
    """Register the eight `browser.*` tools; safe to call more than once.

    Already registered tools are left untouched, so importing this module and
    calling the function explicitly never duplicates or replaces a handler.
    """

    registered: list[Tool] = []
    for tool in _build_tools():
        existing = get(tool.name)
        if existing is None:
            register(tool)
            registered.append(tool)
        else:
            registered.append(existing)
    logger.info("browser araçları hazır: %s", ", ".join(t.name for t in registered))
    return registered


register_browser_tools()
