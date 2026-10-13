#!/usr/bin/env python3
"""Kişi 5 — araç testi: web.search, memory.*, reminders.* gerçek API ile.

Her tool'u registry üzerinden bir kez çağırır ve sonucu yazdırır.

Kullanım:
    python scripts/test_tools.py

Gerekir: backend/.env içinde BRAVE_API_KEY; internet bağlantısı.
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

# Windows konsolu cp1252 olabilir; Türkçe çıktı çökmesin diye UTF-8'e çevir.
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("_", "-") != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("chcp 65001 >NUL")  # konsol kod sayfası

from backend.db import load_env  # noqa: E402

# Araç modülleri import edilirken registry'ye kaydolurlar:
from backend.tools import memory, reminders, search  # noqa: E402,F401
from backend.tools.registry import ToolContext, get  # noqa: E402


async def _noop_trace(step: dict) -> None:
    return None


def make_ctx(request_id: str) -> Any:
    """ToolContext üretir (K3'ün gerçek registry'si farklı imzaysa SimpleNamespace)."""
    try:
        return ToolContext(request_id=request_id)
    except TypeError:
        return SimpleNamespace(
            request_id=request_id, bridge=None, tab=None, emit_trace=_noop_trace
        )


async def call(name: str, args: dict, ctx: Any) -> dict:
    """Tool'u registry'den bulup çağırır; hatayı dict'e çevirir."""
    tool = get(name)
    if tool is None:
        return {"ok": False, "error": f"tool kayıtlı değil: {name}"}
    try:
        return await tool.handler(args, ctx)
    except Exception as exc:  # noqa: BLE001 — testte çökmesin
        return {"ok": False, "error": f"handler exception: {exc}"}


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> int:
    load_env()
    print("Kişi 5 — araç testi (web.search, memory.*, reminders.*)\n")
    ctx = make_ctx("test_tools")
    results: list[bool] = []

    # --- 1) web.search ------------------------------------------------------
    print("[1] web.search — gerçek Brave araması")
    if not (os.environ.get("BRAVE_API_KEY") or "").strip():
        results.append(check("web.search", False, "BRAVE_API_KEY yok (backend/.env)"))
    else:
        started = time.perf_counter()
        data = await call("web.search", {"query": "en iyi kablosuz kulaklık 2026", "count": 3}, ctx)
        elapsed = time.perf_counter() - started
        items = data.get("results") or []
        ok = bool(data.get("ok")) and len(items) > 0
        first = items[0] if items else {}
        ok = ok and all(key in first for key in ("title", "url", "snippet"))
        results.append(check("web.search", ok, f"{len(items)} sonuç, {elapsed:.2f} sn"))
        for item in items[:3]:
            print(f"       - {item.get('title')} | {item.get('url')}")
            print(f"         {str(item.get('snippet'))[:120]}")
        if not ok:
            print(f"       ham yanıt: {data}")

    # --- 2) memory ----------------------------------------------------------
    print("\n[2] memory.save / memory.search / relevant")
    note = "Doğum günü: Ayşe'nin doğum günü 14 Şubat, hediye almayı unutma."
    saved = await call("memory.save", {"text": note, "tags": ["kişi", "hatırlatma"]}, ctx)
    save_ok = bool(saved.get("ok")) and bool(saved.get("id"))
    results.append(check("memory.save", save_ok, f"id={saved.get('id')}"))
    if not save_ok:
        print(f"       ham yanıt: {saved}")

    found = await call("memory.search", {"query": "doğum günü"}, ctx)
    items = found.get("items") or []
    search_ok = bool(found.get("ok")) and len(items) > 0
    results.append(check("memory.search", search_ok, f"{len(items)} sonuç"))
    for item in items[:3]:
        print(f"       - {item.get('id')}: {str(item.get('text'))[:80]}")
    if not search_ok:
        print(f"       ham yanıt: {found}")

    rel = await memory.relevant("doğum günü", k=3)
    results.append(check("relevant()", len(rel) > 0, f"{len(rel)} metin"))

    # --- 3) reminders -------------------------------------------------------
    print("\n[3] reminders.add / list / due")
    past = await call(
        "reminders.add",
        {"text": "geçmiş zaman", "due_iso": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
        ctx,
    )
    results.append(check("geçmiş zaman reddedildi", past.get("ok") is False, str(past.get("error"))))

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
    results.append(check("reminders.list", list_ok, f"{len(listed_items)} kayıt"))

    # due(): 2 sn sonra dolacak bir hatırlatıcı, bir kez dönmeli
    soon = datetime.now(timezone.utc) + timedelta(seconds=2)
    await call("reminders.add", {"text": "kısa süreli test", "due_iso": soon.isoformat()}, ctx)
    print("       due() için 2.5 sn bekleniyor...")
    await asyncio.sleep(2.5)
    first_due = await reminders.due()
    second_due = await reminders.due()
    short_ids = [r["id"] for r in first_due if r["text"] == "kısa süreli test"]
    again_ids = [r["id"] for r in second_due if r["text"] == "kısa süreli test"]
    due_ok = len(short_ids) == 1 and len(again_ids) == 0
    results.append(check("due() bir kez döndürdü", due_ok,
                         f"ilk: {len(first_due)} kayıt, ikinci: {len(second_due)} kayıt"))

    # --- Özet ---------------------------------------------------------------
    print("\n=== ÖZET ===")
    names = [
        "web.search", "memory.save", "memory.search", "relevant()",
        "geçmiş zaman reddedildi", "reminders.add", "reminders.list", "due() bir kez döndürdü",
    ]
    for name, ok in zip(names, results, strict=False):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
