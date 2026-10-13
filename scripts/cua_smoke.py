"""CUA smoke test.

    python scripts/cua_smoke.py            # real sandbox: start -> screenshot -> click -> type -> screenshot
    python scripts/cua_smoke.py --offline  # no Docker: perceive/annotate/shots/desktop tools on a synthetic screen

Outputs go to backend/data/shots/.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from backend.cua.annotate import SHOTS_DIR  # noqa: E402

logger = logging.getLogger(__name__)


async def smoke_real() -> None:
    from backend.cua.perceive import format_for_llm, perceive
    from backend.cua.session import get_session

    session = get_session()
    started = time.perf_counter()
    await session.start()
    print(f"[start] sandbox hazır: {time.perf_counter() - started:.1f}s, ekran {session.width}x{session.height}")

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        png = await session.screenshot()
        (SHOTS_DIR / "smoke_1_raw.png").write_bytes(png)
        print(f"[shot] smoke_1_raw.png ({len(png)} bayt)")

        await session.click(session.width // 2, session.height // 2)
        await session.press("ctrl+alt+t")  # open a terminal on most Linux desktops
        await asyncio.sleep(2)
        await session.type_text("echo Merhaba")
        await session.press("enter")
        await asyncio.sleep(1)

        png = await session.screenshot()
        (SHOTS_DIR / "smoke_2_raw.png").write_bytes(png)
        print(f"[shot] smoke_2_raw.png ({len(png)} bayt)")

        started = time.perf_counter()
        known = getattr(session, "known_elements", None)  # DESKTOP_FAKE=1: no OCR
        snapshot = await perceive(png, "smoke", 2, boxes=known() if known else None)
        print(f"[perceive] {len(snapshot['elements'])} öğe, {time.perf_counter() - started:.2f}s -> {snapshot['shot']}")
        print(format_for_llm(snapshot)[:1500])
    finally:
        await session.close()


def _synthetic_screen() -> bytes:
    """A fake 1280x800 desktop with a text editor window and a taskbar."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int) -> ImageFont.ImageFont:
        for name in ("arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    img = Image.new("RGB", (1280, 800), (58, 110, 165))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 760, 1280, 800], fill=(30, 30, 30))
    for x, label in ((10, "Applications"), (160, "Terminal"), (280, "Text Editor"), (1150, "14:02")):
        d.text((x, 770), label, fill="white", font=font(18))
    d.rectangle([200, 100, 1000, 600], fill="white", outline=(90, 90, 90))
    d.rectangle([200, 100, 1000, 135], fill=(225, 225, 225))
    d.text((215, 107), "Untitled Document - Text Editor", fill="black", font=font(18))
    for x, label in ((215, "File"), (275, "Edit"), (335, "View"), (400, "Search"), (480, "Help")):
        d.text((x, 145), label, fill="black", font=font(18))
    d.line([200, 175, 1000, 175], fill=(200, 200, 200))
    d.text((215, 190), "Merhaba dunya, bu bir deneme metnidir.", fill="black", font=font(20))
    for x, y, label in ((40, 40, "Home"), (40, 140, "Trash")):
        d.text((x, y), label, fill="white", font=font(18))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeSession:
    def __init__(self, png: bytes) -> None:
        self.png, self.calls = png, []

    async def screenshot(self) -> bytes:
        return self.png

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    async def type_text(self, text: str) -> None:
        self.calls.append(("type", text))

    async def press(self, combo: str) -> None:
        self.calls.append(("press", combo))

    async def scroll(self, direction: str) -> None:
        self.calls.append(("scroll", direction))


async def smoke_offline() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cua import perceive as perceive_mod
    from backend.cua.shots_router import router

    png = _synthetic_screen()

    started = time.perf_counter()
    await asyncio.to_thread(perceive_mod.warm_up)
    print(f"[ocr] model yüklendi: {time.perf_counter() - started:.1f}s")
    timings = []
    for n in (1, 2):
        started = time.perf_counter()
        snapshot = await perceive_mod.perceive(png, "r_offline", n)
        timings.append(time.perf_counter() - started)
    print(f"[perceive] {len(snapshot['elements'])} öğe, süre {timings[-1]:.2f}s (hedef < 2s) -> {snapshot['shot']}")
    print(perceive_mod.format_for_llm(snapshot))
    names = [e["name"] for e in snapshot["elements"]]
    assert any("File" == n or n.startswith("File") for n in names), names
    assert any("Merhaba" in n for n in names), names
    refs = [e["ref"] for e in snapshot["elements"]]
    assert refs == list(range(1, len(refs) + 1))
    ys = [e["bbox"][1] for e in snapshot["elements"]]
    assert ys[0] <= ys[-1], "öğeler yukarıdan aşağı sıralı değil"
    assert timings[-1] < 2.0, f"snapshot çok yavaş: {timings[-1]:.2f}s"

    client = TestClient(FastAPI(routes=router.routes))
    r = client.get(snapshot["shot"])
    print(f"[shots] GET {snapshot['shot']} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)} bayt")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    for bad in ("..%2F..%2Fdb.py", "x.txt", "..png", "a%5Cb.png"):
        code = client.get(f"/shots/{bad}").status_code
        print(f"[shots] GET /shots/{bad} -> {code}")
        assert code in (400, 404), bad
    assert client.get("/shots/missing_1.png").status_code == 404

    os.environ["CUA_ENABLED"] = "true"
    from backend.tools import desktop, registry

    fake = _FakeSession(png)
    desktop.get_session = lambda: fake  # type: ignore[assignment]
    for tool in desktop.TOOLS:  # already registered at import if CUA_ENABLED was true in .env
        if tool.name not in {t.name for t in registry.all_tools()}:
            registry.register(tool)

    traces: list[dict] = []

    async def emit(step: dict) -> None:
        traces.append(step)

    ctx = registry.ToolContext(request_id="r_tools", bridge=None, tab=None, emit_trace=emit)
    snap = (await registry.get("desktop.snapshot").handler({}, ctx))["data"]
    assert snap["source"] == "desktop" and traces and traces[0].evidence[0].startswith("shot:/shots/r_tools_1")
    print(f"[tools] desktop.snapshot -> {len(snap['elements'])} öğe, trace evidence {traces[0]['evidence']}")

    target = next(e for e in snap["elements"] if e["name"].startswith("File"))
    r = await registry.get("desktop.click").handler({"ref": target["ref"]}, ctx)
    x, y, w, h = target["bbox"]
    assert r == {"ok": True} and fake.calls[-1] == ("click", x + w // 2, y + h // 2), fake.calls
    print(f"[tools] desktop.click ref={target['ref']} ('{target['name']}') -> {fake.calls[-1]}")
    print("[tools] desktop.click ref=999 ->", await registry.get("desktop.click").handler({"ref": 999}, ctx))
    print("[tools] desktop.type ->", await registry.get("desktop.type").handler({"text": "Merhaba"}, ctx))
    print("[tools] desktop.key ->", await registry.get("desktop.key").handler({"combo": "ctrl+s"}, ctx))
    print("[tools] desktop.scroll ->", await registry.get("desktop.scroll").handler({"direction": "down"}, ctx))
    print("[tools] desktop.scroll bad ->", await registry.get("desktop.scroll").handler({"direction": "left"}, ctx))
    assert fake.calls[-3:] == [("type", "Merhaba"), ("press", "ctrl+s"), ("scroll", "down")], fake.calls


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="test perception and tools without Docker")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await (smoke_offline() if args.offline else smoke_real())
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
