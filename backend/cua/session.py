"""The ONLY module that talks to the CUA SDK. If the CUA API changes, fix it here.

Verified against cua-sandbox 0.9.0 (pip install "cua[sandbox]"):
- Sandbox.ephemeral(image, local=True, ...) is an async context manager yielding a Sandbox
- Image.linux(kind="container") -> ghcr.io/trycua/linux via DockerRuntime (default kind is a QEMU VM)
- sb.screenshot(format="png") -> bytes, sb.screen.size() -> (w, h)
- sb.mouse.click(x, y), sb.mouse.scroll(x, y, scroll_x, scroll_y)  (+scroll_y = up)
- sb.keyboard.type(text), sb.keyboard.keypress(["ctrl", "l"])
- sb.shell.run(command, timeout=...)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from contextlib import AbstractAsyncContextManager
from typing import Any

logger = logging.getLogger(__name__)

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 800
SCROLL_NOTCHES = 5
START_TIMEOUT_S = 180


class DesktopSession:
    """One Linux desktop sandbox (Docker), started lazily and reused for the app's lifetime."""

    def __init__(self, width: int = SCREEN_WIDTH, height: int = SCREEN_HEIGHT) -> None:
        self.width = width
        self.height = height
        self._cm: AbstractAsyncContextManager[Any] | None = None
        self._sb: Any = None
        self._lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._sb is not None

    async def start(self) -> None:
        async with self._lock:
            if self._sb is not None:
                return
            from cua_sandbox import Image, Sandbox

            logger.info("Starting CUA Linux container sandbox...")
            cm = Sandbox.ephemeral(Image.linux(kind="container"), local=True, telemetry_enabled=False)
            self._sb = await asyncio.wait_for(cm.__aenter__(), timeout=START_TIMEOUT_S)
            self._cm = cm
            await self._fix_resolution()

    async def _fix_resolution(self) -> None:
        """Pin the screen size so ref coordinates stay consistent. Best effort."""
        try:
            size = tuple(await self._sb.screen.size())
            if size != (self.width, self.height):
                await self._sb.shell.run(
                    f"DISPLAY=${{DISPLAY:-:1}} xrandr -s {self.width}x{self.height}", timeout=10
                )
                size = tuple(await self._sb.screen.size())
            self.width, self.height = int(size[0]), int(size[1])
            logger.info("Sandbox screen size: %dx%d", self.width, self.height)
        except Exception as exc:
            logger.warning("Could not read/set sandbox resolution: %s", exc)

    async def _ensure(self) -> Any:
        if self._sb is None:
            await self.start()
        return self._sb

    async def screenshot(self) -> bytes:
        sb = await self._ensure()
        return await sb.screenshot(format="png")

    async def click(self, x: int, y: int) -> None:
        sb = await self._ensure()
        await sb.mouse.click(int(x), int(y))

    async def type_text(self, text: str) -> None:
        sb = await self._ensure()
        await sb.keyboard.type(text)

    async def press(self, combo: str) -> None:
        """combo like "ctrl+l", "enter", "ctrl+shift+t"."""
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        if not keys:
            raise ValueError("empty key combo")
        sb = await self._ensure()
        await sb.keyboard.keypress(keys)

    async def scroll(self, direction: str) -> None:
        if direction not in ("up", "down"):
            raise ValueError(f"invalid direction: {direction}")
        sb = await self._ensure()
        notches = SCROLL_NOTCHES if direction == "up" else -SCROLL_NOTCHES
        await sb.mouse.scroll(self.width // 2, self.height // 2, scroll_x=0, scroll_y=notches)

    async def close(self) -> None:
        async with self._lock:
            cm, self._cm, self._sb = self._cm, None, None
            if cm is not None:
                try:
                    await cm.__aexit__(None, None, None)
                    logger.info("CUA sandbox closed")
                except Exception:
                    logger.exception("Error while closing CUA sandbox")


class FakeDesktopSession:
    """Pillow-drawn fake desktop (DESKTOP_FAKE=1): a tiny text editor. No Docker, no CUA SDK.

    It keeps state: clicking the editor focuses it, typing appends text, "Kaydet" shows a status.
    `known_elements()` returns the ground-truth text boxes so perception works without OCR.
    """

    def __init__(self, width: int = SCREEN_WIDTH, height: int = SCREEN_HEIGHT) -> None:
        self.width, self.height = width, height
        self.started = True
        self.focused = False
        self.text = "Merhaba dünya, bu bir deneme metnidir."
        self.status = "Hazır"
        self.scroll_offset = 0
        self.calls: list[tuple] = []
        self._items: list[dict] = []
        self._render()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @staticmethod
    def _font(size: int):
        from PIL import ImageFont

        for name in ("arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _render(self) -> bytes:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (self.width, self.height), (58, 110, 165))
        d = ImageDraw.Draw(img)
        items: list[dict] = []

        def label(x: int, y: int, text: str, size: int = 18, fill="black") -> None:
            f = self._font(size)
            d.text((x, y), text, fill=fill, font=f)
            x0, y0, x1, y1 = d.textbbox((x, y), text, font=f)
            items.append({"text": text, "bbox": [x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4]})

        d.rectangle([0, self.height - 40, self.width, self.height], fill=(30, 30, 30))
        label(10, self.height - 30, "Uygulamalar", fill="white")
        label(160, self.height - 30, "Terminal", fill="white")
        label(40, 40, "Ana Klasör", fill="white")
        d.rectangle([200, 100, 1000, 600], fill="white", outline=(90, 90, 90))
        d.rectangle([200, 100, 1000, 135], fill=(225, 225, 225))
        label(215, 107, "Adsız Belge - Metin Düzenleyici")
        for x, name in ((215, "Dosya"), (300, "Düzenle"), (410, "Görünüm"), (520, "Kaydet")):
            label(x, 145, name)
        d.line([200, 175, 1000, 175], fill=(200, 200, 200))
        if self.focused:
            d.rectangle([205, 180, 995, 560], outline=(30, 120, 255), width=2)
        lines = (self.text + ("|" if self.focused else "")).split("\n")
        for i, line in enumerate(lines[self.scroll_offset : self.scroll_offset + 12]):
            if line.strip():
                label(215, 190 + i * 28, line, 20)
        label(215, 570, f"Durum: {self.status}", 16)
        self._items = items
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def known_elements(self) -> list[dict]:
        """[{"text", "bbox"}] of everything drawn in the last screenshot (stand-in for OCR)."""
        return [dict(i) for i in self._items]

    async def screenshot(self) -> bytes:
        return self._render()

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))
        self.focused = 205 <= x <= 995 and 180 <= y <= 560
        for it in self._items:
            bx, by, bw, bh = it["bbox"]
            if bx <= x <= bx + bw and by <= y <= by + bh and it["text"] == "Kaydet":
                self.status = "Kaydedildi"

    async def type_text(self, text: str) -> None:
        self.calls.append(("type", text))
        if self.focused:
            self.text += text
            self.status = "Değiştirildi"

    async def press(self, combo: str) -> None:
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        if not keys:
            raise ValueError("empty key combo")
        self.calls.append(("press", combo))
        if keys == ["enter"] and self.focused:
            self.text += "\n"
        elif keys == ["ctrl", "s"]:
            self.status = "Kaydedildi"

    async def scroll(self, direction: str) -> None:
        if direction not in ("up", "down"):
            raise ValueError(f"invalid direction: {direction}")
        self.calls.append(("scroll", direction))
        self.scroll_offset = max(0, self.scroll_offset + (1 if direction == "down" else -1))


def fake_enabled() -> bool:
    return os.getenv("DESKTOP_FAKE", "").strip().lower() in ("1", "true", "yes", "on")


_session: DesktopSession | FakeDesktopSession | None = None


def get_session() -> DesktopSession | FakeDesktopSession:
    global _session
    if _session is None:
        _session = FakeDesktopSession() if fake_enabled() else DesktopSession()
    return _session


def set_session(session: DesktopSession | FakeDesktopSession | None) -> None:
    """Replace the singleton (tests)."""
    global _session
    _session = session
