#!/usr/bin/env python3
"""Person 5 / K5 — tool smoke test: web.search, memory.*, reminders.* against real APIs.

Calls every tool once through the registry and prints the result.

Usage:
    python scripts/test_tools.py           # uses a temporary DB, deleted afterwards
    python scripts/test_tools.py --keep    # keep the temporary DB
    python scripts/test_tools.py --mock    # offline: Brave HTTP is mocked, no key needed

The test never touches the real backend/data/assistant.db: ASSISTANT_DB points
at a scratch file (see backend/db.py).

Requires: BRAVE_API_KEY in backend/.env and a working internet connection.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The Windows console may be cp1252; keep Turkish output from crashing.
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("_", "-") != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("chcp 65001 >NUL")

# Scratch database so the demo data stays clean.
TEST_DB = ROOT / "backend" / "data" / "test_tools.db"

from backend.db import load_env  # noqa: E402

# Importing the tool modules registers them in the registry:
from backend.tools import memory, reminders, search  # noqa: E402,F401
from backend.tools.registry import ToolContext, get  # noqa: E402


async def _noop_trace(step: dict) -> None:
    return None


def make_ctx(request_id: str) -> Any:
    """Build a ToolContext (falls back to SimpleNamespace if K3's differs)."""
    try:
        return ToolContext(request_id=request_id)
    except TypeError:
        return SimpleNamespace(
            request_id=request_id, bridge=None, tab=None, emit_trace=_noop_trace
        )


async def call(name: str, args: dict, ctx: Any) -> dict:
    """Look a tool up in the registry and call it, converting exceptions to dicts."""
    tool = get(name)
    if tool is None:
        return {"ok": False, "error": f"tool not registered: {name}"}
    try:
        return await tool.handler(args, ctx)
    except Exception as exc:  # noqa: BLE001 — the test must not crash
        return {"ok": False, "error": f"handler raised: {exc}"}


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return ok


def cleanup_test_db() -> None:
    """Delete the scratch database (WAL/SHM included)."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{TEST_DB}{suffix}").unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001
            print(f"  [!] could not delete the test database: {exc}")


def install_mock_brave() -> None:
    """Route search.py's httpx traffic to an in-process fake Brave API."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Subscription-Token") == "mock-key"
        n = int(request.url.params.get("count", "5"))
        results = [
            {"title": f"<b>Sonuç</b> {i}", "url": f"https://example.com/{i}",
             "description": f"Özet <strong>{i}</strong> &amp; devamı"}
            for i in range(n)
        ]
        return httpx.Response(200, json={"web": {"results": results}})

    real = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    search.httpx = SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=transport, **kw),
        TimeoutException=httpx.TimeoutException,
        HTTPError=httpx.HTTPError,
    )
    os.environ["BRAVE_API_KEY"] = "mock-key"


async def main() -> int:
    if "--mock" in sys.argv:
        install_mock_brave()
    # Keep the real assistant.db clean (respect an ASSISTANT_DB the user set).
    os.environ.setdefault("ASSISTANT_DB", str(TEST_DB))
    load_env()
    print("Person 5 — tool smoke test (web.search, memory.*, reminders.*)\n")
    print(f"test database: {os.environ['ASSISTANT_DB']}\n")
    ctx = make_ctx("test_tools")
    results: list[bool] = []

    # --- 1) web.search ------------------------------------------------------
    print("[1] web.search — real Brave query")
    if not (os.environ.get("BRAVE_API_KEY") or "").strip():
        results.append(check("web.search", False, "BRAVE_API_KEY missing (backend/.env)"))
        results.append(check("web.search count clamped", False, "skipped (no API key)"))
    else:
        started = time.perf_counter()
        data = await call("web.search", {"query": "en iyi kablosuz kulaklık 2026", "count": 3}, ctx)
        elapsed = time.perf_counter() - started
        items = data.get("results") or []
        ok = bool(data.get("ok")) and len(items) > 0
        first = items[0] if items else {}
        ok = ok and all(key in first for key in ("title", "url", "snippet"))
        results.append(check("web.search", ok, f"{len(items)} results, {elapsed:.2f}s"))
        for item in items[:3]:
            print(f"       - {item.get('title')} | {item.get('url')}")
            print(f"         {str(item.get('snippet'))[:120]}")
        if not ok:
            print(f"       raw response: {data}")

        # Over-long count must be clamped, not rejected.
        capped = await call("web.search", {"query": "test", "count": 999}, ctx)
        results.append(
            check("web.search count clamped", len(capped.get("results") or []) <= 20)
        )

    # --- 2) memory ----------------------------------------------------------
    print("\n[2] memory.save / memory.search / relevant")
    note = "Doğum günü: Ayşe'nin doğum günü 14 Şubat, hediye almayı unutma."
    saved = await call("memory.save", {"text": note, "tags": ["kişi", "hatırlatma"]}, ctx)
    save_ok = bool(saved.get("ok")) and bool(saved.get("id"))
    results.append(check("memory.save", save_ok, f"id={saved.get('id')}"))
    if not save_ok:
        print(f"       raw response: {saved}")

    found = await call("memory.search", {"query": "doğum günü"}, ctx)
    items = found.get("items") or []
    search_ok = bool(found.get("ok")) and len(items) > 0
    results.append(check("memory.search", search_ok, f"{len(items)} results"))
    for item in items[:3]:
        print(f"       - {item.get('id')}: {str(item.get('text'))[:80]}")
    if not search_ok:
        print(f"       raw response: {found}")

    # Turkish casing: a query written with the "wrong" case must still match.
    cased = await call("memory.search", {"query": "DOĞUM GÜNÜ"}, ctx)
    results.append(
        check("memory.search Turkish casing", len(cased.get("items") or []) > 0)
    )

    # Stopwords alone must not return everything.
    stop = await call("memory.search", {"query": "ve ile bir"}, ctx)
    results.append(check("memory.search ignores stopwords", (stop.get("items") or []) == []))

    rel = await memory.relevant("doğum günü", k=3)
    results.append(check("relevant()", len(rel) > 0, f"{len(rel)} texts"))
    results.append(check("relevant() never raises", await memory.relevant("") == []))

    # --- 3) reminders -------------------------------------------------------
    print("\n[3] reminders.add / list / due")
    past = await call(
        "reminders.add",
        {"text": "geçmiş zaman", "due_iso": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
        ctx,
    )
    results.append(check("past time rejected", past.get("ok") is False, str(past.get("error"))))

    bad = await call("reminders.add", {"text": "bozuk", "due_iso": "yarın sabah"}, ctx)
    results.append(check("invalid date rejected", bad.get("ok") is False, str(bad.get("error"))))

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    added = await call(
        "reminders.add",
        {"text": "Yarın 10:00 toplantıyı hatırla", "due_iso": tomorrow.isoformat()},
        ctx,
    )
    add_ok = bool(added.get("ok")) and bool(added.get("id"))
    results.append(check("reminders.add", add_ok, f"id={added.get('id')}"))

    listed = await call("reminders.list", {}, ctx)
    listed_items = listed.get("items") or []
    sample_item = listed_items[0] if listed_items else {}
    list_ok = bool(listed.get("ok")) and all(k in sample_item for k in ("id", "text", "due_iso"))
    results.append(check("reminders.list", list_ok, f"{len(listed_items)} pending"))
    if listed_items:
        print(f"       due_iso example (local time): {sample_item.get('due_iso')}")

    # due(): a reminder 2 s out must be returned exactly once.
    soon = datetime.now(timezone.utc) + timedelta(seconds=2)
    await call("reminders.add", {"text": "kısa süreli test", "due_iso": soon.isoformat()}, ctx)
    print("       waiting 2.5s for due()...")
    await asyncio.sleep(2.5)
    first_due = await reminders.due()
    second_due = await reminders.due()
    short_ids = [r["id"] for r in first_due if r["text"] == "kısa süreli test"]
    again_ids = [r["id"] for r in second_due if r["text"] == "kısa süreli test"]
    due_ok = len(short_ids) == 1 and len(again_ids) == 0
    results.append(check("due() returns once", due_ok,
                         f"first: {len(first_due)}, second: {len(second_due)}"))

    # --- summary ------------------------------------------------------------
    print("\n=== SUMMARY ===")
    names = [
        "web.search", "web.search count clamped", "memory.save", "memory.search",
        "memory.search Turkish casing", "memory.search ignores stopwords",
        "relevant()", "relevant() never raises", "past time rejected",
        "invalid date rejected", "reminders.add", "reminders.list", "due() returns once",
    ]
    for name, ok in zip(names, results, strict=True):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if "--keep" in sys.argv:
        print(f"\n  test database kept: {TEST_DB}")
    else:
        cleanup_test_db()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
