"""Calls every K5 tool once through the registry and prints the results.

Usage (from repo root):
    python scripts/test_tools.py             # temp database, real Brave API if BRAVE_API_KEY is set
    python scripts/test_tools.py --real-db   # write into backend/data/assistant.db instead
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from backend import db  # noqa: E402
from backend.tools import memory, reminders, search  # noqa: E402,F401  (imports register the tools)
from backend.tools.registry import ToolContext, get  # noqa: E402

logger = logging.getLogger(__name__)


async def _noop_trace(step: dict) -> None:
    return None


CTX = ToolContext(request_id="r_test", bridge=None, tab=None, emit_trace=_noop_trace)


async def call(name: str, args: dict) -> dict:
    result = await get(name).handler(args, CTX)
    print(f"{name}({json.dumps(args, ensure_ascii=False)})\n  -> {json.dumps(result, ensure_ascii=False)[:600]}")
    return result


async def test_memory() -> None:
    print("\n== memory ==")
    assert memory.turkish_lower("İSTANBUL IŞIK") == "istanbul ışık"
    await call("memory.save", {"text": "Annemin doğum günü 5 Mayıs", "tags": ["aile"]})
    await call("memory.save", {"text": "En sevdiğim kahve filtre kahve"})
    await call("memory.save", {"text": "Ofis adresi: İstiklal Caddesi No 10"})

    result = await call("memory.search", {"query": "doğum günü"})
    assert result["items"] and "doğum günü" in result["items"][0]["text"], result
    result = await call("memory.search", {"query": "istiklal"})  # İ in stored text
    assert result["items"] and "İstiklal" in result["items"][0]["text"], result
    assert (await call("memory.search", {"query": "uzay gemisi"}))["items"] == []
    assert (await call("memory.save", {"text": "  "}))["ok"] is False

    print("relevant('annemin doğum günü ne zaman') ->", await memory.relevant("annemin doğum günü ne zaman"))


async def test_reminders() -> None:
    print("\n== reminders ==")
    tz = reminders.local_tz()
    tomorrow_10 = (datetime.now(tz) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    result = await call("reminders.add", {"text": "Toplantı", "due_iso": tomorrow_10.strftime("%Y-%m-%dT%H:%M:%S")})
    assert result["ok"], result

    soon = datetime.now(tz) + timedelta(seconds=2)
    result = await call("reminders.add", {"text": "Çay demlendi", "due_iso": soon.isoformat(timespec="seconds")})
    assert result["ok"], result

    assert (await call("reminders.add", {"text": "Geçmiş", "due_iso": "2020-01-01T10:00:00"}))["ok"] is False
    assert (await call("reminders.add", {"text": "Bozuk", "due_iso": "yarın 10"}))["ok"] is False

    listed = await call("reminders.list", {})
    assert [i["text"] for i in listed["items"]] == ["Çay demlendi", "Toplantı"], listed
    assert listed["items"][1]["due_iso"].endswith("+03:00") or tz.key != "Europe/Istanbul", listed

    print("due() hemen:", await reminders.due())
    await asyncio.sleep(3)
    fired = await reminders.due()
    print("due() 3 sn sonra:", fired)
    assert [r["text"] for r in fired] == ["Çay demlendi"], fired
    again = await reminders.due()
    print("due() tekrar:", again)
    assert again == [], "due() aynı hatırlatmayı iki kez döndürdü"
    assert [i["text"] for i in (await call("reminders.list", {}))["items"]] == ["Toplantı"]


async def test_search() -> None:
    print("\n== web.search ==")
    assert search.strip_html("<strong>Kablosuz</strong> kulaklık &amp; ses") == "Kablosuz kulaklık & ses"
    assert (await call("web.search", {"query": " "}))["ok"] is False

    if not os.getenv("BRAVE_API_KEY", "").strip():
        result = await call("web.search", {"query": "kablosuz kulaklık"})
        assert result["ok"] is False and "BRAVE_API_KEY" in result["error"]
        os.environ["BRAVE_API_KEY"] = "invalid-test-key"
        try:
            result = await call("web.search", {"query": "kablosuz kulaklık"})  # real API, rejected key
        finally:
            del os.environ["BRAVE_API_KEY"]
        assert result["ok"] is False and "anahtarı geçersiz" in result["error"], result
        print("ATLANDI: BRAVE_API_KEY yok, gerçek sonuç testi yapılmadı (hata yolları doğrulandı)")
        return

    result = await call("web.search", {"query": "en iyi kablosuz kulaklık 2026", "count": 3})
    assert "results" in result, result
    assert 0 < len(result["results"]) <= 3
    for item in result["results"]:
        assert item["title"] and item["url"].startswith("http"), item
        assert "<" not in item["snippet"], item


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-db", action="store_true", help="use backend/data/assistant.db")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if not args.real_db:
        db.DB_PATH = Path(tempfile.mkdtemp(prefix="k5_tools_")) / "assistant.db"
    print(f"DB: {db.DB_PATH}")

    await test_memory()
    await test_reminders()
    await test_search()
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
