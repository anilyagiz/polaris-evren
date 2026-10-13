"""SQLite bağlantı yardımcısı (Kişi 5 sahibi).

Tek veritabanı dosyası: backend/data/assistant.db
- Klasör yoksa oluşturulur.
- Her modül kendi tablosunu CREATE TABLE IF NOT EXISTS ile açar.
- backend/.env dosyasını (K3'ün main.py yüklememiş olsa bile) bir kez okur.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# backend/ dizini ve veri yolları
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "assistant.db"

_ENV_LOADED = False


def load_env() -> None:
    """backend/.env dosyasını os.environ'a bir kez yükler.

    Mevcut ortam değişkenlerini ezmez (gerçek os.environ önce gelir).
    python-dotenv'e gerek yoktur; K3 load_dotenv çağırmamış olsa bile
    modüller tek başına çalışsın diye burada desteklenir.
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
            # Tırnak içindeki değer aynen kalır (yorum işlemez).
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            # Satır içi yorum: boşluk+# ile başlayan kısım atılır.
            # Örn: STT_MODEL=small   # not  ->  "small"
            parts = re.split(r"\s+#", value, maxsplit=1)
            value = parts[0]
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def get_conn() -> sqlite3.Connection:
    """assistant.db için bağlantı açar (klasör yoksa oluşturur).

    Kapatmayı unutma; kısa yol için session() kullan:
        with session() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ...")
    """
    load_env()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Bağlantıyı transaction ile açar; sonunda commit edip kapatır.

    with session() as conn:
        conn.execute("INSERT ...")
    # Hata olursa rollback + close otomatik.
    """
    conn = get_conn()
    try:
        with conn:  # commit / rollback
            yield conn
    finally:
        conn.close()
