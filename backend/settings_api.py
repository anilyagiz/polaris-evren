"""LLM settings from the panel: paste a key, pick a model, no file editing.  Owner: K3.

Endpoints (loopback + extension/localhost origin only):

* ``GET  /settings/llm``         current LLM settings + presets (never the key itself)
* ``PUT  /settings/llm``         validate, write ``backend/.env`` atomically, apply live
* ``POST /settings/llm/test``    one tiny real chat completion
* ``GET  /settings/llm/models``  model list from ``{base_url}/models`` or the preset

Request bodies and secret values are never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import config
from .config import BACKEND_DIR, get_settings

logger = logging.getLogger(__name__)

#: Files this module writes; tests monkeypatch these.
ENV_PATH: Path = BACKEND_DIR / ".env"
ENV_EXAMPLE_PATH: Path = BACKEND_DIR / ".env.example"

MAX_BODY_BYTES = 4096
TEST_TIMEOUT_S = 20.0
MODELS_TIMEOUT_S = 5.0
TEST_MAX_TOKENS = 20

PRESETS: list[dict[str, Any]] = [
    {
        "id": "evren",
        "label": "EVREN",
        "base_url": "https://evren-llmapi.ssyz.org.tr/v1",
        "key_hint": "evren_llm_…",
        "models": [
            {"id": "gemma-4-31b", "label": "Gemma 4 31B", "note": "Hızlı yanıtlar, günlük işler için ideal"},
            {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash", "note": "Daha güçlü, daha yavaş"},
        ],
        "recommended": {"model": "gemma-4-31b", "fallback_model": "deepseek-v4.1-flash", "thinking": "off"},
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_hint": "sk-…",
        "models": [
            {"id": "deepseek-chat", "label": "DeepSeek Chat", "note": "Genel amaçlı"},
            {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner", "note": "Akıl yürütme, yavaş"},
        ],
        "recommended": {"model": "deepseek-chat", "fallback_model": None, "thinking": "on"},
    },
    {"id": "custom", "label": "Diğer (OpenAI uyumlu)", "base_url": "", "key_hint": "", "models": [], "recommended": None},
]

_ALLOWED_ORIGIN = re.compile(
    r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?)$"
)
_URL_RE = re.compile(r"^https?://[^\s\"'#<>\\]+$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$")
_SECRET_RE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no whitespace
_SECRET_FORBIDDEN = set("\"'#\\`")
_OFF = {"off", "0", "false", "no"}

_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip().lower()
    return host in {"::1", "localhost"} or host.startswith("127.") or host.startswith("::ffff:127.")


async def _guard(request: Request) -> None:
    host = request.client.host if request.client else None
    if not _is_loopback(host):
        logger.warning("/settings isteği reddedildi (uzak istemci)")
        raise HTTPException(403, "Ayarlara yalnızca bu bilgisayardan (localhost) erişilebilir.")
    origin = request.headers.get("origin")
    if origin and not _ALLOWED_ORIGIN.match(origin):
        logger.warning("/settings isteği reddedildi (izin verilmeyen origin)")
        raise HTTPException(403, "Bu kaynaktan ayar değiştirilemez.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            too_big = int(length) > MAX_BODY_BYTES
        except ValueError:
            too_big = True
        if too_big:
            raise HTTPException(413, "İstek gövdesi çok büyük.")


router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(_guard)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def _preset(preset_id: str | None) -> dict[str, Any] | None:
    return next((p for p in PRESETS if p["id"] == preset_id), None)


def _provider_for(base_url: str) -> str:
    norm = _norm_url(base_url)
    for preset in PRESETS:
        if preset["base_url"] and _norm_url(preset["base_url"]) == norm:
            return preset["id"]
    return "custom"


def _secret_hint(secret: str) -> str | None:
    secret = (secret or "").strip()
    if not secret:
        return None
    return "…" + secret[-4:] if len(secret) > 8 else "…"


def _current_payload() -> dict[str, Any]:
    s = get_settings()
    fallback = (os.environ.get("LLM_FALLBACK_MODEL") or "").strip() or None
    thinking = "off" if (os.environ.get("LLM_THINKING") or "").strip().lower() in _OFF else "on"
    return {
        "presets": PRESETS,
        "provider": _provider_for(s.deepseek_base_url),
        "base_url": s.deepseek_base_url,
        "model": s.llm_model,
        "fallback_model": fallback,
        "thinking": thinking,
        "api_key_set": s.llm_configured,
        "api_key_hint": _secret_hint(s.deepseek_api_key),
        "brave_key_set": s.brave_configured,
        "llm_configured": s.llm_configured,
    }


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "İstek gövdesi çok büyük.")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Geçersiz JSON gövdesi.") from None
    if not isinstance(data, dict):
        raise HTTPException(422, "Gövde bir JSON nesnesi olmalı.")
    return data


def _check_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, "Sunucu adresi (base_url) boş olamaz.")
    value = value.strip()
    if len(value) > 300:
        raise HTTPException(422, "Sunucu adresi çok uzun (en fazla 300 karakter).")
    if not _URL_RE.match(value):
        raise HTTPException(422, "Sunucu adresi http:// veya https:// ile başlamalı ve boşluk içermemeli.")
    return value


def _check_model(value: Any, label: str = "Model adı") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, f"{label} boş olamaz.")
    value = value.strip()
    if len(value) > 128:
        raise HTTPException(422, f"{label} çok uzun (en fazla 128 karakter).")
    if not _MODEL_RE.match(value):
        raise HTTPException(422, f"{label} geçersiz karakter içeriyor.")
    return value


def _check_secret(value: Any, label: str) -> str | None:
    """Stripped secret, or None when the field means 'leave unchanged'."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422, f"{label} metin olmalı.")
    value = value.strip()
    if not value:
        return None
    if any(ch.isspace() for ch in value):
        raise HTTPException(422, f"{label} boşluk veya satır sonu içeremez.")
    if len(value) > 512:
        raise HTTPException(422, f"{label} çok uzun (en fazla 512 karakter).")
    if not _SECRET_RE.match(value) or _SECRET_FORBIDDEN & set(value):
        raise HTTPException(422, f"{label} geçersiz karakter içeriyor.")
    return value


# ---------------------------------------------------------------------------
# .env writing
# ---------------------------------------------------------------------------
def _render_env(text: str, updates: dict[str, str]) -> str:
    """Replace/add ``KEY=value`` lines, keeping every other line and comment intact."""

    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    done: set[str] = set()
    for i, line in enumerate(lines):
        match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=(.*?)(\r?\n)?$", line, re.S)
        if not match:
            continue
        indent, key, rest, eol = match.groups()
        if key not in updates or key in done:
            continue
        comment = ""
        stripped = rest.strip()
        if stripped[:1] not in ('"', "'"):
            m = re.search(r"\s+#.*$", rest)
            if m:  # keep an inline comment, padded like before
                comment = m.group(0)
        lines[i] = f"{indent}{key}={updates[key]}{comment}{eol or ''}"
        done.add(key)
    missing = [k for k in updates if k not in done]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        for key in missing:
            lines.append(f"{key}={updates[key]}{newline}")
    return "".join(lines)


def _write_env(updates: dict[str, str]) -> None:
    path = Path(ENV_PATH)
    source = path if path.is_file() else Path(ENV_EXAMPLE_PATH)
    # bytes -> str keeps the file's own newline convention untouched
    text = source.read_bytes().decode("utf-8") if source.is_file() else ""
    bom = text.startswith("﻿")
    new_text = ("﻿" if bom else "") + _render_env(text[1:] if bom else text, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _apply_live(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        os.environ[key] = value
    config.reload_settings()
    try:
        from .llm import reload_llm

        reload_llm()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("LLM istemcisi yenilenemedi: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/llm")
async def get_llm_settings() -> dict[str, Any]:
    return _current_payload()


@router.put("/llm")
async def put_llm_settings(request: Request) -> dict[str, Any]:
    body = await _json_body(request)
    updates: dict[str, str] = {}

    if body.get("base_url") is not None:
        updates["DEEPSEEK_BASE_URL"] = _check_url(body["base_url"])
    if body.get("model") is not None:
        updates["LLM_MODEL"] = _check_model(body["model"])
    if "fallback_model" in body:
        fb = body.get("fallback_model")
        if fb is None or (isinstance(fb, str) and not fb.strip()):
            updates["LLM_FALLBACK_MODEL"] = ""
        else:
            updates["LLM_FALLBACK_MODEL"] = _check_model(fb, "Yedek model adı")
    if body.get("thinking") is not None:
        thinking = body["thinking"]
        if thinking not in ("on", "off"):
            raise HTTPException(422, "thinking yalnızca \"on\" veya \"off\" olabilir.")
        updates["LLM_THINKING"] = thinking
    key = _check_secret(body.get("api_key"), "API anahtarı")
    if key is not None:
        updates["DEEPSEEK_API_KEY"] = key
    if "brave_api_key" in body:
        if body["brave_api_key"] is None:
            updates["BRAVE_API_KEY"] = ""
        else:
            brave = _check_secret(body["brave_api_key"], "Brave API anahtarı")
            if brave is not None:
                updates["BRAVE_API_KEY"] = brave

    if updates:
        with _write_lock:
            try:
                _write_env(updates)
            except OSError as exc:
                logger.error("backend/.env yazılamadı: %s", type(exc).__name__)
                raise HTTPException(500, "Ayarlar dosyaya yazılamadı (backend/.env).") from None
            _apply_live(updates)
        logger.info("LLM ayarları güncellendi: %s", ", ".join(sorted(updates)))
    return _current_payload()


def _seconds_tr(ms: int) -> str:
    return f"{ms / 1000:.1f}".replace(".", ",")


def _probe_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if isinstance(exc, asyncio.TimeoutError) or type(exc).__name__ in {"APITimeoutError", "AsyncTimeout"}:
        return f"Sunucu {int(TEST_TIMEOUT_S)} sn içinde yanıt vermedi."
    if status in (401, 403):
        return f"Anahtar reddedildi ({status}). Anahtarı kontrol et."
    if status == 404:
        return "Model bulunamadı (404). Model adını ve sunucu adresini kontrol et."
    if status == 429:
        return "İstek sınırına takıldı (429). Biraz bekleyip tekrar dene."
    if isinstance(status, int) and status >= 500:
        return f"Sunucu hata verdi ({status}). Biraz sonra tekrar dene."
    if isinstance(status, int):
        return f"Sunucu isteği reddetti ({status}). Model adını kontrol et."
    return "Sunucuya ulaşılamadı. Adresi ve internet bağlantını kontrol et."


async def _chat_probe(base_url: str, api_key: str, model: str) -> None:
    """One tiny completion; raises the SDK exception on failure."""

    from openai import AsyncOpenAI

    from .llm import _with_thinking

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=TEST_TIMEOUT_S, max_retries=0)
    try:
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": "Sadece 'tamam' yaz."}],
            "max_tokens": TEST_MAX_TOKENS,
            "temperature": 0,
        }
        await asyncio.wait_for(
            client.chat.completions.create(**_with_thinking(kwargs, model)), TEST_TIMEOUT_S + 1
        )
    finally:
        await client.close()


def _key_may_go_to(url: str, saved_url: str) -> bool:
    """The stored key is only ever sent to the saved endpoint or a built-in preset."""

    norm = url.rstrip("/").lower()
    allowed = {saved_url.rstrip("/").lower()} | {
        str(p["base_url"]).rstrip("/").lower() for p in PRESETS if p.get("base_url")
    }
    return norm in allowed


@router.post("/llm/test")
async def test_llm_settings(request: Request) -> dict[str, Any]:
    body = await _json_body(request)
    s = get_settings()
    base_url = _check_url(body["base_url"]) if body.get("base_url") else s.deepseek_base_url
    model = _check_model(body["model"]) if body.get("model") else s.llm_model
    typed_key = _check_secret(body.get("api_key"), "API anahtarı")
    api_key = typed_key or (s.deepseek_api_key if _key_may_go_to(base_url, s.deepseek_base_url) else "")
    if not api_key:
        message = (
            "API anahtarı girilmemiş."
            if typed_key is None and _key_may_go_to(base_url, s.deepseek_base_url)
            else "Bu adres için API anahtarını gir (kayıtlı anahtar yalnızca kayıtlı adrese gönderilir)."
        )
        return {"ok": False, "latency_ms": 0, "model": model, "message": message}
    started = time.perf_counter()
    try:
        await _chat_probe(base_url, api_key, model)
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        logger.info("LLM bağlantı testi başarısız: %s (%s)", type(exc).__name__, getattr(exc, "status_code", "-"))
        return {"ok": False, "latency_ms": latency, "model": model, "message": _probe_message(exc)}
    latency = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "latency_ms": latency,
        "model": model,
        "message": f"Bağlantı başarılı · {model} · {_seconds_tr(latency)} sn",
    }


async def _fetch_remote_models(base_url: str, api_key: str) -> list[str]:
    import httpx

    async with httpx.AsyncClient(timeout=MODELS_TIMEOUT_S) as client:
        response = await client.get(
            base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {api_key}"}
        )
    response.raise_for_status()
    data = response.json()
    items = data.get("data") if isinstance(data, dict) else data
    ids: list[str] = []
    for item in items or []:
        mid = item.get("id") if isinstance(item, dict) else item
        if isinstance(mid, str) and mid.strip() and mid not in ids:
            ids.append(mid.strip())
    return ids[:200]


@router.get("/llm/models")
async def list_llm_models(base_url: str | None = None, preset: str | None = None) -> dict[str, Any]:
    s = get_settings()
    url = (base_url or "").strip() or s.deepseek_base_url
    preset_obj = _preset(preset) or _preset(_provider_for(url))
    fallback = {"models": list(preset_obj["models"]) if preset_obj else [], "source": "preset"}
    if (
        not _URL_RE.match(url)
        or len(url) > 300
        or not s.deepseek_api_key
        or not _key_may_go_to(url, s.deepseek_base_url)
    ):
        return fallback
    try:
        ids = await _fetch_remote_models(url, s.deepseek_api_key)
    except Exception as exc:
        logger.info("Model listesi alınamadı: %s", type(exc).__name__)
        return fallback
    if not ids:
        return fallback
    return {"models": [{"id": mid, "label": mid, "note": ""} for mid in ids], "source": "api"}
