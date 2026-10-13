"""Unit tests for the real-CUA adapter code paths, without Docker or OCR models.

The real path itself is verified by `python scripts/cua_smoke.py` against a live container.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.cua import perceive as perceive_mod
from backend.cua import session as session_mod


def _pts(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_rapidocr3_output_split_and_join():
    """Menu words far apart become separate elements; normal word spacing stays one element."""
    out = SimpleNamespace(
        txts=("Search Help", "Merhaba dunya", "x"),
        scores=(0.99, 0.98, 0.2),  # last one is below MIN_SCORE
        boxes=[_pts(389, 138, 523, 171), _pts(215, 187, 361, 216), _pts(0, 0, 5, 5)],
        word_results=(
            (("Search", 0.99, _pts(401, 138, 461, 169)), ("Help", 0.99, _pts(481, 140, 521, 170))),
            (("Merhaba", 0.99, _pts(221, 187, 293, 216)), ("dunya", 0.99, _pts(303, 187, 361, 216))),
            (("x", 0.2, _pts(0, 0, 5, 5)),),
        ),
    )
    boxes = perceive_mod._boxes_from_output(out)
    assert [b["text"] for b in boxes] == ["Search", "Help", "Merhaba dunya"]
    names = [e["name"] for e in perceive_mod.to_elements(perceive_mod.merge_lines(boxes))]
    assert names == ["Search", "Help", "Merhaba dunya"]


def test_rapidocr3_output_without_word_boxes_and_empty():
    out = SimpleNamespace(txts=("File",), scores=(0.9,), boxes=[_pts(10, 10, 40, 30)], word_results=())
    assert perceive_mod._boxes_from_output(out) == [{"text": "File", "bbox": [10, 10, 30, 20], "score": 0.9}]
    empty = SimpleNamespace(txts=None, scores=None, boxes=None, word_results=())
    assert perceive_mod._boxes_from_output(empty) == []


class _FakeSandbox:
    """Mimics the cua_sandbox.Sandbox surface session.py uses."""

    def __init__(self, focus_error: bool = False) -> None:
        self.calls: list = []
        self.focus_error = focus_error
        outer = self

        class _Kb:
            async def type(self, text):
                if outer.focus_error:
                    raise RuntimeError("env: target unavailable: no window has keyboard focus (NotFound)")
                outer.calls.append(("type", text))

            async def keypress(self, keys):
                if outer.focus_error:
                    raise RuntimeError("no window has keyboard focus")
                outer.calls.append(("keypress", keys))

        class _Mouse:
            async def click(self, x, y):
                outer.calls.append(("click", x, y))

            async def scroll(self, x, y, scroll_x=0, scroll_y=3):
                outer.calls.append(("scroll", x, y, scroll_x, scroll_y))

        class _Shell:
            async def run(self, command, timeout=30):
                outer.calls.append(("shell", command))
                return SimpleNamespace(stdout="", stderr="", returncode=0)

        self.keyboard, self.mouse, self.shell = _Kb(), _Mouse(), _Shell()

    async def screenshot(self, format="png"):
        return b"\x89PNG"


def _session_with(sb):
    s = session_mod.DesktopSession()
    s._sb = sb
    return s


def test_real_session_maps_calls_to_sandbox():
    sb = _FakeSandbox()
    s = _session_with(sb)

    async def go():
        await s.click(10.6, 20)
        await s.type_text("Merhaba")
        await s.press("Ctrl+L")
        await s.scroll("down")
        await s.scroll("up")
        return await s.screenshot()

    assert asyncio.run(go()) == b"\x89PNG"
    assert sb.calls == [
        ("click", 10, 20),
        ("type", "Merhaba"),
        ("keypress", ["ctrl", "l"]),
        ("scroll", 640, 400, 0, -session_mod.SCROLL_NOTCHES),
        ("scroll", 640, 400, 0, session_mod.SCROLL_NOTCHES),
    ]


def test_real_session_focus_error_falls_back_to_xdotool():
    sb = _FakeSandbox(focus_error=True)
    s = _session_with(sb)
    asyncio.run(s.type_text("it's ok"))
    asyncio.run(s.press("ctrl+enter"))
    shell = [c[1] for c in sb.calls if c[0] == "shell"]
    assert "xdotool type --delay 12 -- 'it'\"'\"'s ok'" in shell[0]
    assert shell[1].endswith("xdotool key -- ctrl+Return")


def test_windows_docker_host_default(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(session_mod.sys, "platform", "win32")
    session_mod._prepare_docker_env()
    assert session_mod.os.environ["DOCKER_HOST"] == session_mod.WINDOWS_DOCKER_HOST
    monkeypatch.setenv("DOCKER_HOST", "tcp://x:2375")
    session_mod._prepare_docker_env()
    assert session_mod.os.environ["DOCKER_HOST"] == "tcp://x:2375"
