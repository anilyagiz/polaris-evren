"""Process-wide logging: UTF-8 safe console + rotating file.  Owner: K3 (P3).

`setup_logging()` is idempotent; `backend.main` and `python -m backend` call it.
The file log lives at ``backend/data/logs/polaris.log`` (git-ignored).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "polaris.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

_MARKER = "_polaris_handler"


def ensure_utf8_console() -> None:
    """Windows cp125x consoles print mojibake for Turkish text; force UTF-8.

    `errors="replace"` guarantees a log line can never crash on an encoding error.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest capture objects, redirected streams...
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - exotic stream
            pass


def _level() -> int:
    name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    value = logging.getLevelName(name)
    return value if isinstance(value, int) else logging.INFO


def setup_logging(log_file: Path | None = LOG_FILE, *, console: bool = True) -> Path | None:
    """Attach console + rotating file handlers to the root logger once.

    Returns the log file path (or `None` when the file could not be opened —
    a read-only checkout must never stop the backend from starting).
    """

    ensure_utf8_console()
    root = logging.getLogger()
    root.setLevel(_level())
    formatter = logging.Formatter(LOG_FORMAT)
    existing = {getattr(h, _MARKER, None) for h in root.handlers}

    if console and "console" not in existing:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        setattr(handler, _MARKER, "console")
        root.addHandler(handler)

    if log_file is None:
        return None
    if "file" in existing:
        return log_file
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("Log dosyası açılamadı (%s): %s", log_file, exc)
        return None
    file_handler.setFormatter(formatter)
    setattr(file_handler, _MARKER, "file")
    root.addHandler(file_handler)
    return log_file
