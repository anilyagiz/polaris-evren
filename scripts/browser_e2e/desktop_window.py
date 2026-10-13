"""Drive the real Polaris desktop window (pywebview/WebView2) and screenshot it.

Prerequisite: the tray runs with ``POLARIS_WEBVIEW_DEBUG_PORT=9333``::

    $env:POLARIS_WEBVIEW_DEBUG_PORT='9333'; .venv\\Scripts\\pythonw -m tray

Then::

    .venv\\Scripts\\python scripts\\browser_e2e\\desktop_window.py "Yarın 9'da spor salonu hatırlat"

Sends the message through the panel UI (textarea + send button), waits for the
final answer, and saves ``scripts/browser_e2e/out/desktop-*.png``: one grab of
the real window rect (PIL ImageGrab, includes the native title bar) and one
CDP page capture.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import websockets

OUT = Path(__file__).resolve().parent / "out"

try:  # physical pixels, so window rects match what PrintWindow renders
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass


def page_ws(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        pages = json.loads(resp.read())
    for page in pages:
        if page.get("type") == "page" and "/app/" in page.get("url", ""):
            return page["webSocketDebuggerUrl"]
    raise SystemExit(f"/app/ sayfası bulunamadı: {[p.get('url') for p in pages]}")


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
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def js(self, expr: str):
        result = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")


def window_rect(title: str = "Polaris") -> tuple[int, int, int, int] | None:
    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    rect = wintypes.RECT()
    # DWM extended frame bounds = the visible window without the invisible resize border.
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def print_window(title: str = "Polaris"):
    """Capture the window itself (PrintWindow, PW_RENDERFULLCONTENT) even when
    other apps cover it — the user may be working in front of it."""

    from PIL import Image

    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    for name, res in (("GetWindowDC", wintypes.HDC), ("CreateCompatibleDC", wintypes.HDC),
                      ("CreateCompatibleBitmap", wintypes.HBITMAP), ("SelectObject", wintypes.HGDIOBJ)):
        getattr(user32 if name == "GetWindowDC" else gdi32, name).restype = res
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    wdc = user32.GetWindowDC(wintypes.HWND(hwnd))
    mdc = gdi32.CreateCompatibleDC(wintypes.HDC(wdc))
    bmp = gdi32.CreateCompatibleBitmap(wintypes.HDC(wdc), width, height)
    gdi32.SelectObject(wintypes.HDC(mdc), wintypes.HGDIOBJ(bmp))
    ok = user32.PrintWindow(wintypes.HWND(hwnd), wintypes.HDC(mdc), 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(wintypes.HDC(mdc), wintypes.HBITMAP(bmp), 0, height, buffer, ctypes.byref(header), 0)
    gdi32.DeleteObject(wintypes.HGDIOBJ(bmp))
    gdi32.DeleteDC(wintypes.HDC(mdc))
    user32.ReleaseDC(wintypes.HWND(hwnd), wintypes.HDC(wdc))
    if not ok:
        return None
    image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    # Trim the invisible resize border (DWM frame bounds vs. window rect).
    frame = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(frame),
                                                  ctypes.sizeof(frame)) == 0:
        image = image.crop((frame.left - rect.left, frame.top - rect.top,
                            frame.right - rect.left, frame.bottom - rect.top))
    return image


def grab(name: str) -> Path | None:
    """Screen grab of the real window rect (native title bar included).

    WebView2 content is composited by DWM, so PrintWindow misses parts of it;
    instead the window is made topmost for a moment (works without foreground
    rights while the user is busy in another app), grabbed, then restored.
    """

    from PIL import ImageGrab

    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = user32.FindWindowW(None, "Polaris")
    path = OUT / name
    if not hwnd:
        print("[desktop] pencere bulunamadı (FindWindow)")
        return None
    flags = 0x0001 | 0x0002 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
    user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), 0, 0, 0, 0, flags)  # HWND_TOPMOST
    try:
        time.sleep(0.8)
        rect = window_rect()
        image = ImageGrab.grab(bbox=rect, all_screens=True)
    finally:
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-2), 0, 0, 0, 0, flags)  # NOTOPMOST
    image.save(path)
    print(f"[desktop] ekran görüntüsü: {path} {image.size}")
    return path


async def run(message: str, port: int, timeout: float) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    async with websockets.connect(page_ws(port), max_size=50_000_000, ping_interval=None) as ws:
        cdp = CDP(ws)
        await cdp.call("Runtime.enable")
        for _ in range(60):
            state = await cdp.js("document.documentElement.dataset.runtime + '|' + "
                                 "!!document.querySelector('textarea')")
            if state == "desktop|true":
                break
            await asyncio.sleep(0.5)
        print(f"[desktop] runtime/textarea: {state}")
        grab("desktop-01-welcome.png")
        before = await cdp.js("document.querySelectorAll('.exchange').length")
        typed = await cdp.js(f"""(() => {{
            const ta = document.querySelector('textarea');
            const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
            setter.call(ta, {json.dumps(message)});
            ta.dispatchEvent(new Event('input', {{bubbles: true}}));
            return ta.value;
        }})()""")
        await asyncio.sleep(0.3)
        await cdp.js("document.querySelector('button[aria-label=\"Mesajı gönder\"]').click()")
        print(f"[desktop] gönderildi: {typed!r}")
        deadline = time.monotonic() + timeout
        answer = ""
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            info = await cdp.js("""(() => {
                const ex = [...document.querySelectorAll('.exchange')].at(-1);
                if (!ex) return null;
                const working = !!ex.querySelector('.working, .spin');
                const text = ex.querySelector('.assistant-message')?.innerText || '';
                return {working, text};
            })()""")
            if info and not info["working"] and info["text"].strip():
                answer = info["text"]
                break
        print(f"[desktop] exchange sayısı {before} -> {await cdp.js('document.querySelectorAll(\".exchange\").length')}")
        print("[desktop] yanıt:\n" + answer[:800])
        await cdp.js("window.scrollTo(0, document.body.scrollHeight)")
        grab("desktop-02-conversation.png")
        shot = await cdp.call("Page.captureScreenshot", format="png")
        page_path = OUT / "desktop-03-page.png"
        page_path.write_bytes(base64.b64decode(shot["data"]))
        print(f"[desktop] sayfa görüntüsü: {page_path}")
        return 0 if answer else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="?", default="Yarın 9'da spor salonu hatırlat")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--timeout", type=float, default=150.0)
    args = parser.parse_args()
    return asyncio.run(run(args.message, args.port, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
