"""memory.save / memory.search tools and relevant() for the orchestrator."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import closing
from datetime import datetime, timezone

from backend import db
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "ve", "veya", "ile", "bir", "bu", "şu", "o", "de", "da", "mi", "mı", "mu", "mü",
    "ne", "için", "gibi", "çok", "daha", "en", "ki", "ben", "benim", "sen", "ama",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    text_norm TEXT NOT NULL
)
"""


def turkish_lower(text: str) -> str:
    """Lowercase with Turkish rules: İ -> i, I -> ı (str.lower() turns İ into 'i̇')."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _keywords(query: str) -> list[str]:
    words = _WORD.findall(turkish_lower(query))
    seen: dict[str, None] = {}
    for word in words:
        if len(word) >= 2 and word not in _STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


def _escape_like(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _save_sync(text: str, tags: list[str]) -> int:
    norm = turkish_lower(" ".join([text, *tags]))
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.get_conn()) as conn, conn:
        conn.execute(_SCHEMA)
        cur = conn.execute(
            "INSERT INTO memories (text, tags, created_at, text_norm) VALUES (?, ?, ?, ?)",
            (text, json.dumps(tags, ensure_ascii=False), created_at, norm),
        )
        return int(cur.lastrowid)


def _search_sync(query: str, limit: int) -> list[dict]:
    words = _keywords(query)
    if not words:
        return []
    where = " OR ".join("text_norm LIKE ? ESCAPE '\\'" for _ in words)
    params = [f"%{_escape_like(w)}%" for w in words]
    with closing(db.get_conn()) as conn:
        conn.execute(_SCHEMA)
        rows = conn.execute(
            f"SELECT id, text, created_at, text_norm FROM memories WHERE {where}", params
        ).fetchall()

    scored = [(sum(w in row["text_norm"] for w in words), row) for row in rows]
    scored.sort(key=lambda item: (item[0], item[1]["created_at"], item[1]["id"]), reverse=True)
    return [
        {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}
        for _, row in scored[:limit]
    ]


async def save(text: str, tags: list[str] | None = None) -> int:
    return await asyncio.to_thread(_save_sync, text, tags or [])


async def search(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    return await asyncio.to_thread(_search_sync, query, limit)


async def relevant(query: str, k: int = 3) -> list[str]:
    """Texts of the k memories most related to query (for prompt context). Never raises."""
    try:
        return [item["text"] for item in await search(query, limit=k)]
    except Exception:
        logger.exception("memory.relevant failed")
        return []


async def _handle_save(args: dict, ctx: ToolContext) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "Kaydedilecek not boş olamaz."}
    tags = args.get("tags") or []
    if not isinstance(tags, list):
        return {"ok": False, "error": "tags bir metin listesi olmalı."}
    try:
        memory_id = await save(text, [str(t).strip() for t in tags if str(t).strip()])
    except Exception as exc:
        logger.exception("memory.save failed")
        return {"ok": False, "error": f"Not kaydedilemedi: {exc}"}
    return {"ok": True, "id": memory_id}


async def _handle_search(args: dict, ctx: ToolContext) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Arama sorgusu boş olamaz."}
    try:
        return {"items": await search(query)}
    except Exception as exc:
        logger.exception("memory.search failed")
        return {"ok": False, "error": f"Hafızada arama yapılamadı: {exc}"}


register(
    Tool(
        name="memory.save",
        description="Kullanıcının hatırlanmasını istediği bir bilgiyi kalıcı hafızaya kaydeder "
        "(ör. doğum günleri, tercihler, adresler). İsteğe bağlı etiketler eklenebilir.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Kaydedilecek bilgi, tam cümle olarak."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "İsteğe bağlı etiketler."},
            },
            "required": ["text"],
        },
        risk="safe",
        handler=_handle_save,
    )
)

register(
    Tool(
        name="memory.search",
        description="Daha önce hafızaya kaydedilmiş notlarda anahtar kelimeyle arar ve en ilgili en fazla 5 notu döndürür.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Aranacak kelimeler, ör. 'doğum günü'."},
            },
            "required": ["query"],
        },
        risk="safe",
        handler=_handle_search,
    )
)
