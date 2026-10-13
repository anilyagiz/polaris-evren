"""memory.save / memory.search — kalıcı not hafızası (Kişi 5 sahibi).

Sözleşmeler:
    memory.save  {text, tags?: [str]} -> {ok, id}                    risk: safe
    memory.search {query} -> {ok, items: [{id, text, created_at}]}   risk: safe
    ayrıca K3 için: async def relevant(query, k=3) -> list[str]

Tablo: memories(id, text, tags, created_at)
Türkçe küçük harfe dikkat: SQLite lower() İ/ı dönüştürmez, bu yüzden
eşleştirme Python tarafında tr_lower ile yapılır (LIKE ile aynı semantik).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.db import load_env, session
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[0-9a-zA-ZçğıöşüÇĞİÖŞÜ]+")
_MAX_RESULTS = 5

_table_ready = False


def _tr_lower(text: str) -> str:
    """Türkçe duyarlı küçük harfe çevirir (İ→i, I→ı)."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    with session() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
    _table_ready = True


def _score(query: str, limit: int) -> list[Any]:
    """Sorgu kelimeleriyle eşleşen satırları puanlayıp döndürür (skor > 0)."""
    _ensure_table()
    words = [w for w in _WORD_RE.findall(_tr_lower(query)) if len(w) >= 2]
    if not words:
        return []
    with session() as conn:
        rows = conn.execute(
            "SELECT id, text, tags, created_at FROM memories ORDER BY created_at DESC"
        ).fetchall()
    scored: list[tuple[int, str, Any]] = []
    for row in rows:
        haystack = _tr_lower(f"{row['text']} {row['tags']}")
        score = sum(1 for word in words if word in haystack)
        if score > 0:
            scored.append((score, str(row["created_at"]), row))
    # Önce eşleşme sayısına göre azalan, eşitse tarihe göre azalan sıralama.
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[:limit]]


async def _save(args: dict, ctx: ToolContext) -> dict:
    """Notu hafızaya kaydeder."""
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text boş olamaz."}
    tags_raw = args.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    tags = [str(tag).strip() for tag in tags_raw if str(tag).strip()]

    load_env()
    _ensure_table()
    mem_id = f"mem_{uuid.uuid4().hex[:8]}"
    created_at = _now_iso()
    with session() as conn:
        conn.execute(
            "INSERT INTO memories (id, text, tags, created_at) VALUES (?, ?, ?, ?)",
            (mem_id, text, json.dumps(tags, ensure_ascii=False), created_at),
        )
    logger.info("memory.save: %s (%d etiket)", mem_id, len(tags))
    return {"ok": True, "id": mem_id}


async def _search(args: dict, ctx: ToolContext) -> dict:
    """Hafızada en fazla 5 ilgili notu döndürür."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query boş olamaz."}
    rows = _score(query, _MAX_RESULTS)
    items = [
        {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}
        for row in rows
    ]
    logger.info("memory.search: %r -> %d sonuç", query, len(items))
    return {"ok": True, "items": items}


async def relevant(query: str, k: int = 3) -> list[str]:
    """K3 için: en ilgili notların metinlerini döndürür (varsayılan 3)."""
    if not query.strip():
        return []
    rows = _score(query, max(1, k))
    return [str(row["text"]) for row in rows]


memory_save_tool = Tool(
    name="memory.save",
    description="Bilgiyi kalıcı hafızaya kaydeder (not, tercih, kişisel bilgi). "
                "İleride hatırlamak gerekecek şeyleri buraya yaz.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Kaydedilecek not (Türkçe)"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiketler (isteğe bağlı), örn. ['kişi', 'tercih']",
            },
        },
        "required": ["text"],
    },
    risk="safe",
    handler=_save,
)

memory_search_tool = Tool(
    name="memory.search",
    description="Hafızada daha önce kaydedilen notlarda arama yapar; "
                "en fazla 5 ilgili notu kimliği ve tarihiyle döndürür.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Aranacak kelime veya cümle"},
        },
        "required": ["query"],
    },
    risk="safe",
    handler=_search,
)

register(memory_save_tool)
register(memory_search_tool)
