"""reminders.add / reminders.list — hatırlatıcılar (Kişi 5 sahibi).

Sözleşmeler:
    reminders.add  {text, due_iso} -> {ok, id}        risk: safe
    reminders.list {} -> {ok, items: [{id, text, due_iso}]}  risk: safe
    ayrıca K3 için: async def due() -> list[dict]
      - zamanı gelmiş ve bildirilmemiş hatırlatmaları döndürür,
      - döndüğü kayıtları notified=1 olarak işaretler (bir kez bildirilir).
      - K3 bunu 30 sn'de bir çağırıp "notify" frame'i gönderir.

Tablo: reminders(id, text, due_at, notified)
due_iso saat dilimi içermiyorsa TIMEZONE varsayılır; geçmiş zaman hatadır.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.db import load_env, session
from backend.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)

_table_ready = False


def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    with session() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    _table_ready = True


def _tz() -> ZoneInfo | timezone:
    """TIMEZONE ortam değişkenini çözü; çözülemezse sabit +03:00."""
    load_env()
    name = (os.environ.get("TIMEZONE") or "Europe/Istanbul").strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("TIMEZONE=%s çözülemedi; +03:00 kullanılıyor", name)
        return timezone.utc


def _parse_due(due_iso: str) -> datetime | str:
    """due_iso'yu bilinçli (aware) zamana çevirir; hata mesajı döndürebilir."""
    try:
        dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
    except ValueError:
        return f"Geçersiz tarih/saat biçimi: {due_iso!r} (ISO 8601 bekleniyor)"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt


async def _add(args: dict, ctx: ToolContext) -> dict:
    """Hatırlatıcı ekler."""
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text boş olamaz."}
    due_raw = str(args.get("due_iso") or "").strip()
    if not due_raw:
        return {"ok": False, "error": "due_iso gerekli (ISO 8601, örn. 2026-10-14T10:00:00+03:00)."}

    parsed = _parse_due(due_raw)
    if isinstance(parsed, str):
        return {"ok": False, "error": parsed}
    due = parsed
    if due <= datetime.now(due.tzinfo):
        return {"ok": False, "error": "Geçmiş bir zaman seçilemez; lütfen ileri bir saat girin."}

    load_env()
    _ensure_table()
    rem_id = f"rem_{uuid.uuid4().hex[:8]}"
    due_at = due.astimezone(timezone.utc).isoformat(timespec="microseconds")
    with session() as conn:
        conn.execute(
            "INSERT INTO reminders (id, text, due_at, notified) VALUES (?, ?, ?, 0)",
            (rem_id, text, due_at),
        )
    logger.info("reminders.add: %s -> %s", rem_id, due_at)
    return {"ok": True, "id": rem_id}


async def _list(args: dict, ctx: ToolContext) -> dict:
    """Kurulan hatırlatıcıları listeler."""
    load_env()
    _ensure_table()
    with session() as conn:
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders ORDER BY due_at"
        ).fetchall()
    items = [
        {"id": row["id"], "text": row["text"], "due_iso": row["due_at"]}
        for row in rows
    ]
    return {"ok": True, "items": items}


async def due() -> list[dict]:
    """Zamanı gelmiş ve bildirilmemiş hatırlatmaları döndürür.

    Döndüğü her kaydı notified=1 olarak işaretler; aynı hatırlatıcı
    ikinci kez asla dönmez. K3 30 sn'de bir çağırır.
    """
    load_env()
    _ensure_table()
    now_utc = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with session() as conn:
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders "
            "WHERE notified = 0 AND due_at <= ? ORDER BY due_at",
            (now_utc,),
        ).fetchall()
        if rows:
            conn.execute(
                "UPDATE reminders SET notified = 1 WHERE notified = 0 AND due_at <= ?",
                (now_utc,),
            )
    if rows:
        logger.info("due(): %d hatırlatıcı bildirime hazır", len(rows))
    return [
        {"id": row["id"], "text": row["text"], "due_iso": row["due_at"]}
        for row in rows
    ]


reminders_add_tool = Tool(
    name="reminders.add",
    description="İleri bir tarih/saat için hatırlatıcı kurar. due_iso ISO 8601 biçimindedir "
                "(örn. 2026-10-14T10:00:00+03:00); saat dilimi yazılmazsa sunucu ayarı kullanılır. "
                "Geçmiş zaman kabul edilmez.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Hatırlatıcı metni (Türkçe)"},
            "due_iso": {"type": "string", "description": "ISO 8601 tarih ve saat"},
        },
        "required": ["text", "due_iso"],
    },
    risk="safe",
    handler=_add,
)

reminders_list_tool = Tool(
    name="reminders.list",
    description="Kurulan tüm hatırlatıcıları tarih sırasıyla listeler.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="safe",
    handler=_list,
)

register(reminders_add_tool)
register(reminders_list_tool)
