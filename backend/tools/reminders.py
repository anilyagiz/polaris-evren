"""reminders.add / reminders.list tools and due() polled by the orchestrator (owner: Person 5 / K5).

Contract (ORTAK BAĞLAM §7):
    reminders.add  {text, due_iso} -> {ok, id}                       risk: safe
    reminders.list {}              -> {ok, items: [{id, text, due_iso}]}  risk: safe
    plus, for K3:  async def due() -> list[dict]
      - returns reminders whose time has come and marks them notified,
      - each reminder is returned exactly once,
      - K3 polls this every 30 s and emits a "notify" frame.

Table: reminders(id, text, due_at, notified)

``due_at`` is stored as a UTC ISO string with a fixed format, so lexicographic
string comparison is equivalent to comparing instants. Outgoing ``due_iso``
values are converted back to the user's local time (TIMEZONE).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend import db
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
)
"""

#: Used when TIMEZONE cannot be resolved AND tzdata is missing (Turkey has no DST).
_FALLBACK_TZ = timezone(timedelta(hours=3))


def local_tz() -> ZoneInfo | timezone:
    """Resolve TIMEZONE; fall back to a fixed +03:00 offset so this never raises."""
    db.load_env()
    name = (os.getenv("TIMEZONE") or "Europe/Istanbul").strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning(
            "TIMEZONE=%r could not be resolved; using a fixed +03:00 offset (is tzdata installed?)",
            name,
        )
        return _FALLBACK_TZ


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table, migrating a legacy ``id TEXT PRIMARY KEY`` table.

    ``CREATE TABLE IF NOT EXISTS`` would leave an old table untouched, inserts
    would store NULL ids and ``UPDATE ... WHERE id = ?`` would match nothing —
    which would make every reminder fire on every poll. Rebuild instead.
    """
    conn.execute(_SCHEMA)
    columns = {
        row["name"]: (row["type"] or "").upper()
        for row in conn.execute("PRAGMA table_info(reminders)")
    }
    if columns.get("id") != "INTEGER":
        logger.warning("reminders.id is %s; rebuilding the table with an integer id", columns.get("id"))
        conn.execute("ALTER TABLE reminders RENAME TO reminders_legacy")
        conn.execute(_SCHEMA)
        legacy = conn.execute(
            "SELECT text, due_at, notified FROM reminders_legacy ORDER BY due_at, rowid"
        ).fetchall()
        for row in legacy:
            conn.execute(
                "INSERT INTO reminders (text, due_at, notified) VALUES (?, ?, ?)",
                (row["text"], row["due_at"], row["notified"]),
            )
        conn.execute("DROP TABLE reminders_legacy")


def _to_utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _to_local_iso(utc_str: str) -> str:
    """Convert a stored UTC stamp to the user's local time (panel shows 10:00, not 07:00)."""
    try:
        dt = datetime.fromisoformat(utc_str)
    except ValueError:
        return utc_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz()).isoformat(timespec="seconds")


def parse_due(due_iso: str) -> datetime:
    """Parse an ISO 8601 string; naive values are interpreted in TIMEZONE. Raises ValueError."""
    dt = datetime.fromisoformat(due_iso.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt


def _row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "text": row["text"], "due_iso": _to_local_iso(row["due_at"])}


def _add_sync(text: str, due: datetime) -> int:
    with closing(db.get_conn()) as conn, conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, _to_utc_str(due))
        )
        return int(cur.lastrowid)


def _list_sync() -> list[dict]:
    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders WHERE notified = 0 ORDER BY due_at, id"
        ).fetchall()
    return [_row(r) for r in rows]


def _due_sync() -> list[dict]:
    """Select and mark due reminders atomically so nothing is notified twice."""
    now = _to_utc_str(datetime.now(timezone.utc))
    with closing(db.get_conn()) as conn:
        _ensure_schema(conn)
        # BEGIN IMMEDIATE takes the write lock up front; two pollers cannot both
        # read the same pending row. DDL above may already have opened a
        # transaction, so only start one when needed.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, text, due_at FROM reminders "
                "WHERE notified = 0 AND due_at <= ? ORDER BY due_at, id",
                (now,),
            ).fetchall()
            conn.executemany(
                "UPDATE reminders SET notified = 1 WHERE id = ?", [(r["id"],) for r in rows]
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return [_row(r) for r in rows]


async def add(text: str, due: datetime) -> int:
    """Store a reminder and return its id (runs off the event loop)."""
    return await asyncio.to_thread(_add_sync, text, due)


async def list_pending() -> list[dict]:
    """Reminders that have not fired yet (runs off the event loop)."""
    return await asyncio.to_thread(_list_sync)


async def due() -> list[dict]:
    """Return reminders whose time has come and mark them notified (each returned once)."""
    return await asyncio.to_thread(_due_sync)


async def _handle_add(args: dict, ctx: ToolContext) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "Hatırlatma metni boş olamaz."}
    try:
        due_at = parse_due(str(args.get("due_iso") or ""))
    except ValueError:
        return {"ok": False, "error": "Geçersiz tarih. ISO 8601 biçiminde olmalı, ör. 2026-10-14T10:00:00."}
    if due_at <= datetime.now(timezone.utc):
        return {"ok": False, "error": "Hatırlatma zamanı geçmişte olamaz."}
    try:
        reminder_id = await add(text, due_at)
    except Exception as exc:  # noqa: BLE001 — handlers return errors, never raise
        logger.exception("reminders.add failed")
        return {"ok": False, "error": f"Hatırlatma kaydedilemedi: {exc}"}
    return {"ok": True, "id": reminder_id}


async def _handle_list(args: dict, ctx: ToolContext) -> dict:
    try:
        return {"ok": True, "items": await list_pending()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reminders.list failed")
        return {"ok": False, "error": f"Hatırlatmalar okunamadı: {exc}"}


register(
    Tool(
        name="reminders.add",
        description="Belirtilen zamanda kullanıcıya hatırlatma bildirimi gönderir. due_iso ISO 8601 olmalı; "
        "saat dilimi verilmezse kullanıcının yerel saati (TIMEZONE) kabul edilir. Geçmiş zaman kabul edilmez.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Hatırlatma metni, ör. 'Toplantı'."},
                "due_iso": {"type": "string", "description": "Zaman, ör. '2026-10-14T10:00:00'."},
            },
            "required": ["text", "due_iso"],
        },
        risk="safe",
        handler=_handle_add,
    )
)

register(
    Tool(
        name="reminders.list",
        description="Henüz zamanı gelip bildirilmemiş hatırlatmaları zamana göre sıralı listeler.",
        parameters={"type": "object", "properties": {}},
        risk="safe",
        handler=_handle_list,
    )
)
