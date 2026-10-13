"""BrowserBridge: request/response correlation between backend and extension.

Owner: K3.  One bridge per WebSocket connection.  A tool handler awaits a
Future; the reader loop resolves it when `tool_result` (or `confirm`) arrives.
A timeout resolves the future as a failure, so a dead extension can never
deadlock a request.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


def new_call_id() -> str:
    return f"c_{secrets.token_hex(3)}"


class ToolTimeout(RuntimeError):
    """Raised internally when the extension does not answer in time."""


class BrowserBridge:
    """Correlates `tool_call` frames with `tool_result` / `confirm` frames."""

    def __init__(self, send: SendFn, request_id: str | None = None) -> None:
        self._send = send
        self.request_id = request_id
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._confirm: dict[str, asyncio.Future[bool]] = {}
        self._closed = False

    # -- lifecycle --------------------------------------------------------
    def set_request_id(self, request_id: str | None) -> None:
        self.request_id = request_id

    async def close(self) -> None:
        """Fail every waiter; called when the socket goes away."""

        self._closed = True
        for call_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result({"ok": False, "error": "bağlantı kapandı"})
            self._pending.pop(call_id, None)
        for call_id, future in list(self._confirm.items()):
            if not future.done():
                future.set_result(False)
            self._confirm.pop(call_id, None)

    # -- tool calls -------------------------------------------------------
    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Send `tool_call` and await `tool_result`."""

        if self._closed:
            return {"ok": False, "error": "bağlantı kapandı"}

        from .config import get_settings

        budget = timeout if timeout is not None else get_settings().tool_timeout_s
        call_id = new_call_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[call_id] = future
        frame = {
            "type": "tool_call",
            "call_id": call_id,
            "request_id": self.request_id,
            "name": name,
            "args": args or {},
        }
        try:
            await self._send(frame)
        except Exception as exc:
            self._pending.pop(call_id, None)
            logger.warning("tool_call gönderilemedi (%s): %s", name, exc)
            return {"ok": False, "error": f"tool_call gönderilemedi: {exc}"}

        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=budget)
        except asyncio.TimeoutError:
            logger.info("tool_call %s (%s) zaman aşımı", call_id, name)
            self._pending.pop(call_id, None)
            return {"ok": False, "error": f"zaman aşımı: {name} {budget:.0f} sn içinde yanıtlamadı"}
        except asyncio.CancelledError:
            self._pending.pop(call_id, None)
            raise
        finally:
            self._pending.pop(call_id, None)
        return result

    def resolve_tool_result(
        self,
        call_id: str,
        ok: bool,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Called by the reader loop for a `tool_result` frame."""

        future = self._pending.pop(call_id, None)
        if future is None or future.done():
            logger.info("Geç/ilgisiz tool_result yok sayıldı: %s", call_id)
            return False
        future.set_result({"ok": bool(ok), "data": data, "error": error})
        return True

    # -- confirmation gate ------------------------------------------------
    async def request_confirm(
        self,
        call_id: str,
        summary: str,
        tool: str,
        args: dict[str, Any],
        timeout: float | None = None,
    ) -> bool:
        """Send `need_confirm`; missing answer inside the budget means "no"."""

        from .config import get_settings

        budget = timeout if timeout is not None else get_settings().confirm_timeout_s
        if self._closed:
            return False
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._confirm[call_id] = future
        frame = {
            "type": "need_confirm",
            "call_id": call_id,
            "request_id": self.request_id,
            "summary": summary,
            "tool": tool,
            "args": args or {},
        }
        try:
            await self._send(frame)
        except Exception as exc:
            self._confirm.pop(call_id, None)
            logger.warning("need_confirm gönderilemedi: %s", exc)
            return False
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=budget)
        except asyncio.TimeoutError:
            logger.info("need_confirm %s zaman aşımı, reddedilmiş sayılıyor", call_id)
            return False
        except asyncio.CancelledError:
            raise
        finally:
            self._confirm.pop(call_id, None)

    def resolve_confirm(self, call_id: str, approved: bool) -> bool:
        """Called by the reader loop for a `confirm` frame."""

        future = self._confirm.pop(call_id, None)
        if future is None or future.done():
            logger.info("Geç/ilgisiz confirm yok sayıldı: %s", call_id)
            return False
        future.set_result(bool(approved))
        return True

    @property
    def pending_count(self) -> int:
        return len(self._pending) + len(self._confirm)
