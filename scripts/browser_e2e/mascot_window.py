"""Drive the real Polaris desktop mascot (pywebview/WebView2) and screenshot it.

Prerequisite: the tray (or Polaris.exe) runs with ``POLARIS_WEBVIEW_DEBUG_PORT=9333``::

    $env:POLARIS_WEBVIEW_DEBUG_PORT='9333'; .venv\\Scripts\\pythonw -m tray --hidden

Then::

    .venv\\Scripts\\python scripts\\browser_e2e\\mascot_window.py "Yarın 8'de koşuya çıkmamı hatırlat"

Clicks the star with the real mouse (opens the bubble), types the task through
CDP, presses Enter, waits for the answer and grabs the mascot's screen region
(PIL ImageGrab, so the colour-keyed transparency is what you see) into
``scripts/browser_e2e/out/mascot-*.png``.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import websockets
from PIL import ImageGrab

OUT = Path(__file__).resolve().parent / "out"
TITLE = "Polaris maskot"
PAD = 40  # desktop around the window, to show the transparency

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass
user32 = ctypes.windll.user32


def window_rect() -> tuple[int, int, int, int]:
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd or not user32.IsWindowVisible(hwnd):
        raise SystemExit("maskot penceresi görünür değil")
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def grab(name: str) -> Path:
    left, top, right, bottom = window_rect()
    path = OUT / f"mascot-{name}.png"
    ImageGrab.grab(bbox=(left - PAD, top - PAD, right + PAD, bottom + PAD), all_screens=True).save(path)
    print("[mascot] kaydedildi:", path)
    return path


def star_point() -> tuple[int, int]:
    """Centre of the star's clickable circle (mascot.css: pet 120x114 at right 14 / bottom 16)."""

    _left, _top, right, bottom = window_rect()
    scale = user32.GetDpiForWindow(user32.FindWindowW(None, TITLE)) / 96
    return int(right - (14 + 60) * scale), int(bottom - (16 + 114 * 0.44) * scale)


def click(x: int, y: int, double: bool = False) -> None:
    user32.SetCursorPos(x, y)
    for _ in range(2 if double else 1):
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
        time.sleep(0.05)


def page_ws(port: int) -> str:
    pages = None
    for host in ("127.0.0.1", "[::1]"):  # WebView2 sometimes binds IPv6 only
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/json", timeout=5) as resp:
                pages = json.loads(resp.read())
            break
        except OSError:
            continue
    if pages is None:
        raise SystemExit(f"WebView2 hata ayıklama portu {port} yanıt vermiyor")
    for page in pages:
        if page.get("type") == "page" and "mascot.html" in page.get("url", ""):
            return page["webSocketDebuggerUrl"]
    raise SystemExit(f"maskot sayfası bulunamadı: {[p.get('url') for p in pages]}")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

    async def call(self, method: str, **params):
        self.seq += 1
        ident = self.seq
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == ident:
                return msg.get("result", {})

    async def js(self, expr: str):
        result = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return result.get("result", {}).get("value")


async def main(task: str, port: int, timeout: float) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    async with websockets.connect(page_ws(port), max_size=None) as ws:
        cdp = CDP(ws)
        for _ in range(40):
            if await cdp.js("document.querySelector('.mascot') ? document.querySelector('.mascot').dataset.status : ''") == "connected":
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(1.0)
        grab("01-idle")

        star = star_point()
        click(*star)
        await asyncio.sleep(1.2)
        if not await cdp.js("!!document.querySelector('.mascot-form input')"):
            raise SystemExit("tıklama balonu açmadı")
        grab("02-bubble")

        await cdp.js("document.querySelector('.mascot-form input').focus()")
        await cdp.call("Input.insertText", text=task)
        await asyncio.sleep(0.3)
        grab("03-typing")
        for kind in ("keyDown", "keyUp"):
            await cdp.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter",
                           windowsVirtualKeyCode=13, nativeVirtualKeyCode=13,
                           **({"text": "\r"} if kind == "keyDown" else {}))
        await asyncio.sleep(1.0)
        print("[mascot] mood:", await cdp.js("document.querySelector('.mascot').dataset.mood"))
        grab("04-working")

        started = time.monotonic()
        answer = None
        while time.monotonic() - started < timeout:
            answer = await cdp.js("(document.querySelector('.mascot-bubble-answer .mascot-text, .mascot-bubble-error .mascot-text, .mascot-bubble-confirm')||{}).innerText || null")
            if answer:
                break
            await asyncio.sleep(0.5)
        if not answer:
            grab("05-timeout")
            raise SystemExit("yanıt gelmedi")
        await asyncio.sleep(0.8)
        print("[mascot] yanıt:", answer.replace("\n", " ")[:300])
        print("[mascot] mood:", await cdp.js("document.querySelector('.mascot').dataset.mood"))
        grab("05-answer")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?", default="Yarın 8'de koşuya çıkmamı hatırlat")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.task, args.port, args.timeout)))
