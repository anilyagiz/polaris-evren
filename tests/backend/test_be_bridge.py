import asyncio

import pytest

from backend.bridge import BrowserBridge, new_call_id


def make_bridge():
    sent = []

    async def send(frame):
        sent.append(frame)

    return BrowserBridge(send, "r_1"), sent


def test_new_call_id_shape():
    a, b = new_call_id(), new_call_id()
    assert a.startswith("c_") and len(a) == 8 and a != b


async def test_call_resolved_by_tool_result():
    br, sent = make_bridge()
    task = asyncio.create_task(br.call("browser.click", {"ref": 1}, timeout=2))
    await asyncio.sleep(0.01)
    frame = sent[0]
    assert frame["type"] == "tool_call" and frame["name"] == "browser.click" and frame["request_id"] == "r_1"
    assert br.resolve_tool_result(frame["call_id"], True, {"x": 1}) is True
    assert await task == {"ok": True, "data": {"x": 1}, "error": None}
    assert br.pending_count == 0
    assert br.resolve_tool_result(frame["call_id"], True) is False  # late duplicate


async def test_call_timeout():
    br, _ = make_bridge()
    res = await br.call("browser.read", {}, timeout=0.05)
    assert res["ok"] is False and "zaman aşımı" in res["error"]
    assert br.pending_count == 0


async def test_close_fails_pending_calls_and_confirms():
    br, sent = make_bridge()
    call = asyncio.create_task(br.call("browser.read", {}, timeout=5))
    conf = asyncio.create_task(br.request_confirm("c_x", "s", "browser.click", {}, timeout=5))
    await asyncio.sleep(0.02)
    assert br.pending_count == 2
    await br.close()
    assert (await call) == {"ok": False, "error": "bağlantı kapandı"}
    assert await conf is False
    assert (await br.call("browser.read", {})) == {"ok": False, "error": "bağlantı kapandı"}
    assert await br.request_confirm("c_y", "s", "t", {}) is False


async def test_cancel_removes_pending():
    br, _ = make_bridge()
    t = asyncio.create_task(br.call("browser.read", {}, timeout=5))
    await asyncio.sleep(0.01)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert br.pending_count == 0


async def test_send_failure_returns_error():
    async def bad(frame):
        raise RuntimeError("closed")

    br = BrowserBridge(bad)
    res = await br.call("browser.read", {}, timeout=1)
    assert res["ok"] is False and "gönderilemedi" in res["error"]
    assert br.pending_count == 0
    assert await br.request_confirm("c", "s", "t", {}, timeout=1) is False


async def test_confirm_approve_deny_timeout():
    br, sent = make_bridge()
    t = asyncio.create_task(br.request_confirm("c_1", "Emin misin?", "browser.click", {"ref": 2}, timeout=2))
    await asyncio.sleep(0.01)
    assert sent[0]["type"] == "need_confirm" and sent[0]["call_id"] == "c_1" and sent[0]["summary"] == "Emin misin?"
    assert br.resolve_confirm("c_1", True) is True
    assert await t is True
    t = asyncio.create_task(br.request_confirm("c_2", "s", "t", {}, timeout=2))
    await asyncio.sleep(0.01)
    br.resolve_confirm("c_2", False)
    assert await t is False
    assert await br.request_confirm("c_3", "s", "t", {}, timeout=0.05) is False
    assert br.resolve_confirm("c_3", True) is False
    assert br.pending_count == 0


def test_set_request_id():
    br, _ = make_bridge()
    br.set_request_id("r_2")
    assert br.request_id == "r_2"
