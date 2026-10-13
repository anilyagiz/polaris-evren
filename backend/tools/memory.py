"""memory.save / memory.search tools and relevant() for the orchestrator (owner: Person 5 / K5).

Contract (ORTAK BAĞLAM §7):
    memory.save   {text, tags?: [str]} -> {ok, id}                     risk: safe
    memory.search {query}              -> {ok, items: [{id, text, created_at}]}  risk: safe
    plus, for K3:  async def relevant(query: str, k: int = 3) -> list[str]

Table: memories(id, text, tags, created_at, text_norm)

Turkish casing note: SQLite's lower() does not fold İ/ı, so the searchable
column stores an already-normalised copy of the text and tags (``text_norm``).
SQLite's ``LIKE`` is ASCII-case-insensitive only, which is fine here because
both sides are pre-lowercased by ``turkish_lower``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
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


def _normalise(text: str, tags: list[str]) -> str:
    return turkish_lower(" ".join([text, *tags]))


def _keywords(query: str) -> list[str]:
    """Split a query into unique keywords, dropping stopwords and 1-char tokens."""
    words = _WORD.findall(turkish_lower(query))
    seen: dict[str, None] = {}
    for word in words:
        if len(word) >= 2 and word not in _STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


def _escape_like(word: str) -> str:
    """Escape LIKE wildcards so a keyword containing % or _ matches literally."""
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table, migrating databases written by the pre-merge version.

    The earlier K5 implementation used ``id TEXT PRIMARY KEY`` and had no
    ``text_norm`` column. ``CREATE TABLE IF NOT EXISTS`` would leave such a
    table alone and every ``UPDATE ... WHERE id = ?`` would silently match
    nothing, so both differences are repaired here instead.
    """
    conn.execute(_SCHEMA)
    columns = {
        row["name"]: (row["type"] or "").upper()
        for row in conn.execute("PRAGMA table_info(memories)")
    }

    if columns.get("id") != "INTEGER":
        logger.warning("memories.id is %s; rebuilding the table with an integer id", columns.get("id"))
        conn.execute("ALTER TABLE memories RENAME TO memories_legacy")
        conn.execute(_SCHEMA)
        legacy = conn.execute(
            "SELECT text, tags, created_at FROM memories_legacy ORDER BY rowid"
        ).fetchall()
        for row in legacy:
            conn.execute(
                "INSERT INTO memories (text, tags, created_at, text_norm) VALUES (?, ?, ?, ?)",
                (row["text"], row["tags"], row["created_at"], _normalise(row["text"], [row["tags"]])),
            )
        conn.execute("DROP TABLE memories_legacy")
        return

    if "text_norm" not in columns:
        logger.warning("memories lacks text_norm; adding and backfilling it")
        conn.execute("ALTER TABLE memories ADD COLUMN text_norm TEXT NOT NULL DEFAULT ''")
        for row in conn.execute("SELECT id, text, tags FROM memories").fetchall():
            conn.execute(
                "UPDATE memories SET text_norm = ? WHERE id = ?",
                (_normalise(row["text"], [row["tags"]]), row["id"]),
            )


def _save_sync(text: str, tags: list[str]) -> int:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.get_conn()) as conn, conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO memories (text, tags, created_at, text_norm) VALUES (?, ?, ?, ?)",
            (text, json.dumps(tags, ensure_ascii=False), created_at, _normalise(text, tags)),
        )
        return int(cur.lastrowid)


def _search_sync(query: str, limit: int) -> list[dict]:
    words = _keywords(query)
    if not words:
        return []
    where = " OR ".join("text_norm LIKE ? ESCAPE '\\'" for _ in words)
    params = [f"%{_escape_like(w)}%" for w in words]
    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT id, text, created_at, text_norm FROM memories WHERE {where}", params
        ).fetchall()

    # Rank by how many keywords matched, then by recency.
    scored = [(sum(w in row["text_norm"] for w in words), row) for row in rows]
    scored.sort(key=lambda item: (item[0], item[1]["created_at"], item[1]["id"]), reverse=True)
    return [
        {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}
        for _, row in scored[:limit]
    ]


async def save(text: str, tags: list[str] | None = None) -> int:
    """Persist a note and return its id (runs off the event loop)."""
    return await asyncio.to_thread(_save_sync, text, tags or [])


async def search(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Return the most relevant notes (runs off the event loop)."""
    return await asyncio.to_thread(_search_sync, query, limit)


async def relevant(query: str, k: int = 3) -> list[str]:
    """Texts of the k memories most related to query (prompt context for K3).

    Never raises: a memory failure must not take the orchestrator down.
    """
    try:
        return [item["text"] for item in await search(query, limit=k)]
    except Exception:  # noqa: BLE001 — deliberately swallowed, see docstring
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
    except Exception as exc:  # noqa: BLE001 — handlers return errors, never raise
        logger.exception("memory.save failed")
        return {"ok": False, "error": f"Not kaydedilemedi: {exc}"}
    return {"ok": True, "id": memory_id}


async def _handle_search(args: dict, ctx: ToolContext) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Arama sorgusu boş olamaz."}
    try:
        return {"ok": True, "items": await search(query)}
    except Exception as exc:  # noqa: BLE001
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
