"""Per-session conversation history (short-term memory).

Table: conversation_turns(id, session_id, request_id, role, text, created_at)

The orchestrator loads the last ``CONVERSATION_TURNS`` turns of the current
session before every request and renders them as a compact "## Önceki konuşma"
block for the agents, so follow-ups such as "peki İzmir'in?" or "ikincisini
aç" can be resolved.  After every ``final`` the user text and the answer are
appended.  All functions are sync (cheap SQLite) with async wrappers that run
off the event loop; none of them ever raises into the request pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from backend import db

logger = logging.getLogger(__name__)

#: Turns injected into agent prompts per request (env CONVERSATION_TURNS).
DEFAULT_TURNS = 10
#: Turns sent to the panel in the `session` event.
RESTORE_TURNS = 20
#: Per-turn clip and total cap of the prompt block (characters).
TURN_CLIP = 400
TOTAL_CAP = 3000
#: Stored text is capped so one huge answer cannot bloat the table.
STORE_CAP = 8000

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_conversation_turns_session "
    "ON conversation_turns(session_id, id)"
)

HISTORY_HEADER = "## Önceki konuşma"
HISTORY_INSTRUCTION = (
    "(Bu oturumdaki son mesajlar, eskiden yeniye. Yeni mesajdaki 'bu', 'onu', 'şunu', "
    "'peki ya …', 'ikincisi', 'aynısını' gibi atıflar ve eksik cümleler bu konuşmaya "
    "göredir; neye atıf yapıldığını buradan çıkar.)"
)


def turns_limit() -> int:
    db.load_env()
    raw = (os.environ.get("CONVERSATION_TURNS") or "").strip()
    try:
        return max(0, min(int(raw), 50)) if raw else DEFAULT_TURNS
    except ValueError:
        return DEFAULT_TURNS


def valid_session_id(value: Any) -> str | None:
    """Return the id when it matches ``[A-Za-z0-9_-]{1,64}``, else None."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SESSION_RE.match(value) else None


def new_session_id() -> str:
    return "s_" + secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ensure(conn) -> None:
    conn.execute(_SCHEMA)
    conn.execute(_INDEX)


def append_turn_sync(session_id: str, role: str, text: str, request_id: str = "") -> None:
    if role not in ("user", "assistant"):
        raise ValueError(f"geçersiz rol: {role}")
    text = (text or "").strip()
    if not text or not valid_session_id(session_id):
        return
    with closing(db.get_conn()) as conn, conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO conversation_turns (session_id, request_id, role, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, request_id or "", role, text[:STORE_CAP], _now()),
        )


def recent_turns_sync(session_id: str, limit: int) -> list[dict[str, Any]]:
    """Last ``limit`` turns of a session, oldest first."""

    if limit <= 0 or not valid_session_id(session_id):
        return []
    with closing(db.get_conn()) as conn, conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT role, text, request_id, created_at FROM conversation_turns "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, int(limit)),
        ).fetchall()
    return [
        {"role": r["role"], "text": r["text"], "request_id": r["request_id"], "ts": r["created_at"]}
        for r in reversed(rows)
    ]


def latest_session_sync() -> dict[str, Any] | None:
    """Most recently active session (for the desktop window's first run)."""

    with closing(db.get_conn()) as conn, conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT session_id, MAX(id) AS last_id, MAX(created_at) AS ts, COUNT(*) AS n "
            "FROM conversation_turns GROUP BY session_id ORDER BY last_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {"session_id": row["session_id"], "updated_at": row["ts"], "turns": int(row["n"])}


async def append_turn(session_id: str, role: str, text: str, request_id: str = "") -> None:
    try:
        await asyncio.to_thread(append_turn_sync, session_id, role, text, request_id)
    except Exception:  # noqa: BLE001 - history must never break a request
        logger.exception("Konuşma geçmişine yazılamadı")


async def recent_turns(session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(
            recent_turns_sync, session_id, turns_limit() if limit is None else limit
        )
    except Exception:  # noqa: BLE001
        logger.exception("Konuşma geçmişi okunamadı")
        return []


async def latest_session() -> dict[str, Any] | None:
    try:
        return await asyncio.to_thread(latest_session_sync)
    except Exception:  # noqa: BLE001
        logger.exception("Son oturum okunamadı")
        return None


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_history(turns: list[dict[str, Any]], *, clip: int = TURN_CLIP, cap: int = TOTAL_CAP) -> str:
    """Compact prompt block; the newest turns win when the total cap is hit."""

    if not turns:
        return ""
    lines: list[str] = []
    used = 0
    for turn in reversed(turns):
        who = "Kullanıcı" if turn.get("role") == "user" else "Asistan"
        line = f"- {who}: {_clip(str(turn.get('text') or ''), clip)}"
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    lines.reverse()
    return "\n".join([HISTORY_HEADER, HISTORY_INSTRUCTION, *lines])
