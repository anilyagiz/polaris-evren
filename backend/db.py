"""Shared SQLite helper. Every module creates its own tables with CREATE TABLE IF NOT EXISTS."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "data" / "assistant.db"


def get_conn() -> sqlite3.Connection:
    """Open a connection to backend/data/assistant.db, creating the folder if needed.

    Connections are cheap; open one per operation (inside asyncio.to_thread) and close it.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
