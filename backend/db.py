"""Shared SQLite helper (owner: Person 5 / K5).

Single database file: ``backend/data/assistant.db``.
- The parent folder is created on demand.
- Every module creates its own tables with ``CREATE TABLE IF NOT EXISTS``.
- ``backend/.env`` is parsed once into ``os.environ`` so the K5 modules work
  standalone even when ``main.py`` does not call ``load_dotenv()``.
- ``ASSISTANT_DB`` overrides the database path; the tests use this so they
  never write into the demo database.

NOTE for K3: if ``llm.py`` reads keys straight from ``os.environ``, call
``backend.db.load_env()`` at the very top of ``main.py`` (before other imports
that read configuration).
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# backend/ directory and data paths
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"

#: Default database file (tests can redirect via ASSISTANT_DB).
DB_PATH = DATA_DIR / "assistant.db"

_ENV_LOADED = False


def load_env() -> None:
    """Load ``backend/.env`` into ``os.environ`` exactly once.

    Real environment variables always win; nothing already set is overwritten.
    A tiny hand-rolled parser is used so the K5 modules have no dependency on
    python-dotenv.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = BACKEND_DIR / ".env"
    if not env_path.is_file():
        return
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'"):
            # Quoted value: keep as-is, no comment stripping inside quotes.
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            # Trailing comment: everything from whitespace+# onwards is dropped.
            # e.g. STT_MODEL=small   # note  ->  "small"
            value = re.split(r"\s+#", value, maxsplit=1)[0]
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def db_path() -> Path:
    """Return the database file to use.

    ``ASSISTANT_DB`` (absolute or relative) takes precedence when set, which is
    how the test scripts stay out of the demo database.
    """
    load_env()
    override = (os.environ.get("ASSISTANT_DB") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DB_PATH


def get_conn() -> sqlite3.Connection:
    """Open a connection to the assistant database, creating the folder if needed.

    Connections are cheap: open one per operation and close it (the K5 modules
    do this inside ``asyncio.to_thread``). Prefer ``session()`` for writes.

        with session() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ...")
    """
    load_env()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Open a connection inside a transaction; commit on success, close always."""
    conn = get_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()
