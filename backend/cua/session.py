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
import logging
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


_session: DesktopSession | None = None


def get_session() -> DesktopSession:
    global _session
    if _session is None:
        _session = DesktopSession()
    return _session
