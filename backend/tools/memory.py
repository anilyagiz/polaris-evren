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
from backend.tools.registry import Tool, ToolContext

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
    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        return _search_conn(conn, query, limit)


def _search_conn(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    words = _keywords(query)
    if not words:
        return []
    where = " OR ".join("text_norm LIKE ? ESCAPE '\\'" for _ in words)
    params = [f"%{_escape_like(w)}%" for w in words]
    rows = conn.execute(
        f"SELECT id, text, created_at, text_norm FROM memories WHERE {where}", params
    ).fetchall()

    # Rank by how many keywords matched, then by recency.
    phrase = " ".join(words)
    scored = [
        (sum(w in row["text_norm"] for w in words) * 2 + (1 if len(words) > 1 and phrase in row["text_norm"] else 0), row)
        for row in rows
    ]
    scored.sort(key=lambda item: (item[0], item[1]["created_at"], item[1]["id"]), reverse=True)
    return [
        {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}
        for _, row in scored[:limit]
    ]


#: Tag of facts the background extractor stored about the user.
PROFILE_TAG = "profil"
#: Two notes this similar (difflib ratio on the normalised text) are duplicates.
DUPLICATE_RATIO = 0.82

_SECRET_PATTERNS = (
    re.compile(
        r"(şifre|sifre|parola|password|passwd|pin kod|cvv|cvc|güvenlik kodu|api[ _-]?key|"
        r"api anahtar|token|secret|gizli anahtar|tc kimlik|t\.c\.|kimlik no|iban|kart numara)",
    ),
    re.compile(r"(?:\d[ -]?){13,19}"),  # card numbers
    re.compile(r"\b[1-9]\d{10}\b"),  # TC kimlik numarası (phone numbers start with 0)
    re.compile(r"\btr\d{2}[ \d]{20,}"),  # IBAN
    re.compile(r"\b(sk|pk|rk|ghp|gho|xox[bap])[-_][a-z0-9_-]{12,}"),  # API keys
    re.compile(r"\b[a-z0-9_\-]{32,}\b"),  # long opaque tokens
)


def is_sensitive(text: str) -> bool:
    """True when a note looks like a secret (password, card, TC kimlik, API key...)."""

    lowered = turkish_lower(text or "")
    return any(pattern.search(lowered) for pattern in _SECRET_PATTERNS)


#: Words that do not change a fact ("Kullanıcı kahveyi hep şekersiz içer" ==
#: "kahveyi şekersiz içer") and are dropped before the similarity check.
_FILLER = {
    "kullanıcı", "kullanıcının", "kullanıcıya", "benim", "ben", "hep", "her", "zaman",
    "genelde", "genellikle", "bu", "arada", "de", "da", "ise", "olarak",
}


def _fact_key(text: str) -> str:
    words = _WORD.findall(turkish_lower(text or ""))
    return " ".join(w for w in words if w not in _FILLER)


def _similar_sync(text: str, threshold: float = DUPLICATE_RATIO) -> dict | None:
    import difflib

    norm = _fact_key(text)
    if not norm:
        return None
    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, text, created_at FROM memories ORDER BY id DESC LIMIT 500"
        ).fetchall()
    best: tuple[float, sqlite3.Row] | None = None
    for row in rows:
        other = _fact_key(row["text"])
        ratio = 1.0 if other == norm else difflib.SequenceMatcher(None, norm, other).ratio()
        if ratio >= threshold and (best is None or ratio > best[0]):
            best = (ratio, row)
    if best is None:
        return None
    row = best[1]
    return {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}


def _profile_sync(limit: int) -> list[dict]:
    """Extracted profile facts first, then the most recent other notes."""

    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, text, created_at FROM memories "
            "ORDER BY (tags LIKE ?) DESC, id DESC LIMIT ?",
            (f'%"{PROFILE_TAG}"%', int(limit)),
        ).fetchall()
    return [{"id": r["id"], "text": r["text"], "created_at": r["created_at"]} for r in rows]


def _forget_sync(memory_id: int | None, query: str, delete_all: bool) -> list[dict]:
    with closing(db.get_conn()) as conn, conn:
        _ensure_schema(conn)
        if delete_all:
            rows = [dict(r) for r in conn.execute("SELECT id, text FROM memories").fetchall()]
        elif memory_id is not None:
            rows = [
                dict(r)
                for r in conn.execute("SELECT id, text FROM memories WHERE id = ?", (memory_id,)).fetchall()
            ]
        else:
            words = _keywords(query)
            if not words:
                return []
            where = " AND ".join("text_norm LIKE ? ESCAPE '\\'" for _ in words)
            params = [f"%{_escape_like(w)}%" for w in words]
            rows = [
                dict(r)
                for r in conn.execute(f"SELECT id, text FROM memories WHERE {where}", params).fetchall()
            ]
            if not rows:
                # No note contains every keyword: only the single best match goes.
                rows = [{"id": b["id"], "text": b["text"]} for b in _search_conn(conn, query, 1)]
        if rows:
            conn.executemany("DELETE FROM memories WHERE id = ?", [(int(r["id"]),) for r in rows])
    return [{"id": int(r["id"]), "text": r["text"]} for r in rows]


async def find_similar(text: str) -> dict | None:
    return await asyncio.to_thread(_similar_sync, text)


async def profile_facts(limit: int = 6) -> list[dict]:
    """Most recent facts the extractor stored about the user (never raises)."""

    try:
        return await asyncio.to_thread(_profile_sync, limit)
    except Exception:  # noqa: BLE001
        logger.exception("memory.profile_facts failed")
        return []


async def forget(*, memory_id: int | None = None, query: str = "", delete_all: bool = False) -> list[dict]:
    """Delete notes by id, by query (all keywords) or everything; returns what was removed."""

    return await asyncio.to_thread(_forget_sync, memory_id, query, delete_all)


async def save_fact(text: str, tags: list[str] | None = None) -> int | None:
    """Store a durable user fact unless it is a secret or a near-duplicate.

    Returns the new id, or None when the fact was skipped.
    """

    text = " ".join((text or "").split())
    if not text or len(text) > 300 or is_sensitive(text):
        return None
    if await find_similar(text) is not None:
        return None
    return await save(text, tags or [PROFILE_TAG])


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
    if is_sensitive(text):
        return {
            "ok": False,
            "error": "Şifre, kart numarası, TC kimlik no veya API anahtarı gibi gizli bilgiler "
            "hafızaya kaydedilmez.",
        }
    try:
        existing = await find_similar(text)
        if existing is not None:
            # Same fact already stored (e.g. by the background extractor).
            return {"ok": True, "id": existing["id"], "duplicate": True}
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
        try:
            limit = max(1, min(int(args.get("limit") or MAX_RESULTS), 20))
        except (TypeError, ValueError):
            limit = MAX_RESULTS
        return {"ok": True, "items": await search(query, limit=limit)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory.search failed")
        return {"ok": False, "error": f"Hafızada arama yapılamadı: {exc}"}


db.register_once(
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

db.register_once(
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


async def _handle_forget(args: dict, ctx: ToolContext) -> dict:
    delete_all = args.get("all") is True or str(args.get("all")).lower() == "true"
    raw_id = args.get("id")
    memory_id: int | None = None
    if raw_id not in (None, ""):
        try:
            memory_id = int(raw_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "id bir sayı olmalı."}
    query = str(args.get("query") or "").strip()
    if not delete_all and memory_id is None and not query:
        return {"ok": False, "error": "Silinecek kayıt için id, query veya all=true gerekli."}
    try:
        removed = await forget(memory_id=memory_id, query=query, delete_all=delete_all)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory.forget failed")
        return {"ok": False, "error": f"Hafıza kaydı silinemedi: {exc}"}
    if not removed:
        return {"ok": False, "error": "Eşleşen hafıza kaydı bulunamadı."}
    return {"ok": True, "deleted": len(removed), "items": removed}


db.register_once(
    Tool(
        name="memory.forget",
        description="Kalıcı hafızadan kayıt siler ('bunu unut', 'hafızanı sil'). id verilirse o "
        "kaydı, query verilirse tüm kelimeleri içeren kayıtları, all=true ise tüm hafızayı "
        "siler. Kullanıcı onayı gerekir.",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Silinecek kaydın numarası (memory.search sonucundan)."},
                "query": {"type": "string", "description": "Silinecek bilgiyi tarif eden kelimeler, ör. 'kahve'."},
                "all": {"type": "boolean", "description": "true ise tüm hafıza silinir."},
            },
        },
        risk="confirm",
        handler=_handle_forget,
    )
)
