"""Fake browser-extension client for the Polaris WebSocket protocol.

Speaks docs/PROTOCOL.md section 6 against a live backend:

* sends `hello` and `user_msg`
* answers `tool_call` from a tiny stateful fake shopping page
* answers `need_confirm` according to a policy (approve / deny / silent)
* records and schema-validates every server event
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import websockets

from .schema import validate_event

ConfirmPolicy = Literal["approve", "deny", "silent"]
ToolPolicy = Literal["answer", "silent", "disconnect"]


class FakePage:
    """Search box (ref 7), product list, form fields and a risky order button (ref 52)."""

    def __init__(self) -> None:
        self.url = "https://shop.example.com/"
        self.title = "Klavye Dünyası"
        self.values: dict[int, str] = {}
        self.executed: list[tuple[str, dict[str, Any]]] = []  # ground truth of side effects
        self.highlighted: list[int] = []
        self.elements: list[dict[str, Any]] = [
            {"ref": 5, "role": "textbox", "name": "Ad Soyad", "value": None, "bbox": [20, 100, 200, 30]},
            {"ref": 6, "role": "textbox", "name": "E-posta", "value": None, "bbox": [20, 140, 200, 30]},
            {"ref": 7, "role": "textbox", "name": "Ürün ara", "value": None, "bbox": [20, 20, 300, 30]},
            {"ref": 8, "role": "button", "name": "Ara", "value": None, "bbox": [330, 20, 60, 30]},
            {"ref": 21, "role": "link", "name": "Keychron K8 Mekanik Klavye - 1.499 TL", "value": None, "bbox": [20, 200, 400, 24]},
            {"ref": 22, "role": "link", "name": "Logitech MX Mechanical - 1.299 TL", "value": None, "bbox": [20, 230, 400, 24]},
            {"ref": 23, "role": "link", "name": "Redragon Kumara - 999 TL", "value": None, "bbox": [20, 260, 400, 24]},
            {"ref": 30, "role": "button", "name": "Sepete ekle", "value": None, "bbox": [440, 200, 100, 30]},
            {"ref": 52, "role": "button", "name": "Siparişi tamamla", "value": None, "bbox": [440, 300, 160, 40]},
        ]
        self.text = (
            "Klavye Dünyası. Keychron K8 Mekanik Klavye 1.499 TL. Logitech MX Mechanical 1.299 TL. "
            "Redragon Kumara 999 TL. Sepetiniz boş."
        )

    def snapshot(self) -> dict[str, Any]:
        elements = []
        for el in self.elements:
            copy = dict(el)
            if copy["ref"] in self.values:
                copy["value"] = self.values[copy["ref"]]
            elements.append(copy)
        return {
            "source": "browser",
            "url": self.url,
            "title": self.title,
            "elements": elements,
            "text_excerpt": self.text[:1500],
            "shot": None,
        }

    def has(self, ref: Any) -> bool:
        return any(el["ref"] == ref for el in self.elements)

    def handle(self, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
        if name == "browser.snapshot":
            return True, self.snapshot(), None
        if name == "browser.read":
            ref = args.get("ref")
            if ref is not None and not self.has(ref):
                return False, None, f"ref {ref} bulunamadı"
            return True, {"text": self.text}, None
        if name in ("browser.click", "browser.type", "browser.highlight"):
            if not self.has(args.get("ref")):
                return False, None, f"ref {args.get('ref')} bulunamadı"
            if name == "browser.highlight":
                self.highlighted.append(int(args["ref"]))
                return True, {"ok": True}, None
            self.executed.append((name, dict(args)))
            if name == "browser.type":
                self.values[int(args["ref"])] = str(args.get("text", ""))
            return True, {"ok": True}, None
        if name == "browser.scroll":
            return True, {"ok": True}, None
        if name == "browser.navigate":
            self.url = str(args.get("url", self.url))
            return True, {"ok": True, "url": self.url, "title": self.title}, None
        return False, None, f"bilinmeyen araç: {name}"

    def executed_names(self) -> list[str]:
        return [n for n, _ in self.executed]


@dataclass
class Run:
    """Everything that happened between one `user_msg` and its terminal event."""

    request_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0
    timed_out: bool = False

    @property
    def traces(self) -> list[dict[str, Any]]:
        return [e["step"] for e in self.events if e["type"] == "trace"]

    def agents(self) -> list[str]:
        return [s["agent"] for s in self.traces]

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == "tool_call"]

    @property
    def confirms(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == "need_confirm"]

    @property
    def final(self) -> dict[str, Any] | None:
        return next((e for e in self.events if e["type"] == "final"), None)

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == "error"]

    def trace_decisions(self) -> list[str]:
        return [f"{s['agent']}: {s['decision']}" for s in self.traces]

    def summary(self) -> str:
        parts = []
        for e in self.events:
            if e["type"] == "trace":
                s = e["step"]
                parts.append(f"trace[{s['agent']}] {s['decision'][:70]}")
            elif e["type"] == "tool_call":
                parts.append(f"tool_call {e['name']} {json.dumps(e['args'], ensure_ascii=False)}")
            elif e["type"] == "need_confirm":
                parts.append(f"need_confirm {e['tool']} {e['summary'][:50]}")
            elif e["type"] == "final":
                parts.append(f"final {e['answer'][:60]!r}")
            elif e["type"] == "error":
                parts.append(f"error {e['message'][:80]!r}")
            else:
                parts.append(e["type"])
        return "\n".join(parts)


class FakeExtension:
    def __init__(
        self,
        ws_url: str,
        *,
        confirm: ConfirmPolicy | Callable[[dict[str, Any]], bool | None] = "approve",
        tools: ToolPolicy = "answer",
        tool_delay_s: float = 0.0,
        page: FakePage | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.confirm_policy = confirm
        self.tools_policy = tools
        self.tool_delay_s = tool_delay_s
        self.page = page or FakePage()
        self.events: list[dict[str, Any]] = []
        self.violations: list[str] = []
        self.notifications: list[dict[str, Any]] = []
        self.closed_by_server = False
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._waiters: list[tuple[Callable[[dict[str, Any]], bool], asyncio.Future[dict[str, Any]]]] = []
        self._seq = 0
        self.on_tool_call: Callable[[dict[str, Any]], Any] | None = None

    # -- connection -------------------------------------------------------
    async def connect(self, hello: bool = True) -> "FakeExtension":
        self._ws = await websockets.connect(self.ws_url, max_size=8 * 1024 * 1024, open_timeout=10)
        self._reader = asyncio.create_task(self._read_loop())
        if hello:
            await self.send({"type": "hello", "client": "extension", "version": "0.1"})
        return self

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass

    async def send(self, frame: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def send_raw(self, text: str) -> None:
        await self._ws.send(text)

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self.closed_by_server and self._ws.close_code is None

    # -- reader -----------------------------------------------------------
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    self.violations.append(f"non-JSON frame from server: {str(raw)[:80]!r}")
                    continue
                problems = validate_event(event)
                if problems:
                    self.violations.extend(problems)
                self.events.append(event)
                if isinstance(event, dict):
                    await self._react(event)
                    self._wake(event)
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            self.violations.append(f"fake extension reader crashed: {exc!r}")
        finally:
            self.closed_by_server = True
            self._wake({"type": "__closed__"})

    def _wake(self, event: dict[str, Any]) -> None:
        for pred, fut in list(self._waiters):
            if not fut.done() and (event.get("type") == "__closed__" or pred(event)):
                fut.set_result(event)
                self._waiters.remove((pred, fut))

    async def _react(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "notify":
            self.notifications.append(event)
        elif kind == "tool_call":
            if self.on_tool_call:
                maybe = self.on_tool_call(event)
                if asyncio.iscoroutine(maybe):
                    await maybe
            if self.tools_policy == "silent":
                return
            if self.tools_policy == "disconnect":
                await self._ws.close()
                return
            asyncio.create_task(self._answer_tool(event))
        elif kind == "need_confirm":
            policy = self.confirm_policy
            decision: bool | None
            if callable(policy):
                decision = policy(event)
            elif policy == "silent":
                decision = None
            else:
                decision = policy == "approve"
            if decision is not None:
                await self.send({"type": "confirm", "call_id": event["call_id"], "approved": bool(decision)})

    async def _answer_tool(self, event: dict[str, Any]) -> None:
        if self.tool_delay_s:
            await asyncio.sleep(self.tool_delay_s)
        ok, data, error = self.page.handle(event["name"], event.get("args") or {})
        try:
            await self.send({"type": "tool_result", "call_id": event["call_id"], "ok": ok, "data": data, "error": error})
        except Exception:
            pass

    # -- requests ---------------------------------------------------------
    def next_id(self) -> str:
        self._seq += 1
        return f"r_e2e{self._seq:02d}"

    async def wait_for(self, pred: Callable[[dict[str, Any]], bool], timeout: float = 30.0, *, since: int = 0) -> dict[str, Any] | None:
        """First event matching `pred` (already received after index `since`, or future). None on timeout/close."""

        for event in self.events[since:]:
            if pred(event):
                return event
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters.append((pred, fut))
        try:
            event = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._waiters = [(p, f) for p, f in self._waiters if f is not fut]
        return None if event.get("type") == "__closed__" else event

    async def start(self, text: str, *, source: str = "text", tab: dict[str, Any] | None | str = "page", request_id: str | None = None) -> tuple[str, int]:
        """Send a user_msg without waiting; returns (request_id, event index)."""

        rid = request_id or self.next_id()
        if tab == "page":
            tab = {"id": 101, "url": self.page.url, "title": self.page.title}
        start_idx = len(self.events)
        await self.send({"type": "user_msg", "id": rid, "text": text, "source": source, "tab": tab})
        return rid, start_idx

    async def ask(
        self,
        text: str,
        *,
        timeout: float = 45.0,
        source: str = "text",
        tab: dict[str, Any] | None | str = "page",
        request_id: str | None = None,
    ) -> Run:
        """Send a user_msg and wait for its `final` or `error`."""

        started = time.monotonic()
        rid, idx = await self.start(text, source=source, tab=tab, request_id=request_id)
        terminal = await self.wait_for(
            lambda e: e.get("type") in ("final", "error") and e.get("request_id") == rid, timeout, since=idx
        )
        run = Run(request_id=rid, events=[e for e in self.events[idx:]], elapsed_s=time.monotonic() - started)
        run.timed_out = terminal is None
        return run

    async def settle(self, seconds: float = 0.4) -> None:
        await asyncio.sleep(seconds)
