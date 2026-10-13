"""reminders.add / reminders.list tools and due() polled by the orchestrator every ~30 s."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend import db
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

# due_at is stored as a UTC ISO string with a fixed format, so string comparison == time comparison.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
)
"""


def local_tz() -> ZoneInfo:
    name = os.getenv("TIMEZONE", "Europe/Istanbul")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown TIMEZONE %r, falling back to Europe/Istanbul", name)
        return ZoneInfo("Europe/Istanbul")


def _to_utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _to_local_iso(utc_str: str) -> str:
    return datetime.fromisoformat(utc_str).astimezone(local_tz()).isoformat(timespec="seconds")


def parse_due(due_iso: str) -> datetime:
    """Parse an ISO 8601 string; naive values are interpreted in TIMEZONE. Raises ValueError."""
    dt = datetime.fromisoformat(due_iso.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt


def _row(row) -> dict:
    return {"id": row["id"], "text": row["text"], "due_iso": _to_local_iso(row["due_at"])}


def _add_sync(text: str, due: datetime) -> int:
    with closing(db.get_conn()) as conn, conn:
        conn.execute(_SCHEMA)
        cur = conn.execute("INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, _to_utc_str(due)))
        return int(cur.lastrowid)


def _list_sync() -> list[dict]:
    with closing(db.get_conn()) as conn:
        conn.execute(_SCHEMA)
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders WHERE notified = 0 ORDER BY due_at, id"
        ).fetchall()
    return [_row(r) for r in rows]


def _due_sync() -> list[dict]:
    now = _to_utc_str(datetime.now(timezone.utc))
    with closing(db.get_conn()) as conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")  # select + mark atomically so each reminder fires once
        try:
            rows = conn.execute(
                "SELECT id, text, due_at FROM reminders WHERE notified = 0 AND due_at <= ? ORDER BY due_at, id",
                (now,),
            ).fetchall()
            conn.executemany("UPDATE reminders SET notified = 1 WHERE id = ?", [(r["id"],) for r in rows])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return [_row(r) for r in rows]


async def add(text: str, due: datetime) -> int:
    return await asyncio.to_thread(_add_sync, text, due)


async def list_pending() -> list[dict]:
    return await asyncio.to_thread(_list_sync)


async def due() -> list[dict]:
    """Return reminders whose time has come and mark them notified (each is returned only once)."""
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
    except Exception as exc:
        logger.exception("reminders.add failed")
        return {"ok": False, "error": f"Hatırlatma kaydedilemedi: {exc}"}
    return {"ok": True, "id": reminder_id}


async def _handle_list(args: dict, ctx: ToolContext) -> dict:
    try:
        return {"items": await list_pending()}
    except Exception as exc:
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
