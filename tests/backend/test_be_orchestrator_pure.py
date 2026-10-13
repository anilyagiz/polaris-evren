"""Pure helpers of the orchestrator (risk gate / protocol parsing)."""
import pytest

from backend import orchestrator as orch
from backend.tools.registry import Tool


async def _h(a, c):
    return {"ok": True}


def tool(name):
    return Tool(name=name, description="d", parameters={"type": "object"}, risk="safe", handler=_h)


@pytest.mark.parametrize("v,exp", [
    (True, True), (False, False), ("false", False), ("False", False), ("true", True), ("evet", True),
    ("no", False), ("", False), (1, True), (0, False), (None, False), ([], False), ({}, False),
])
def test_as_bool_fails_closed(v, exp):
    assert orch._as_bool(v) is exp


def test_as_memories():
    assert orch._as_memories({"results": [{"a": 1}, "x", {"b": 2}]}) == [{"a": 1}, {"b": 2}]
    assert orch._as_memories([{"a": 1}] * 5, limit=2) == [{"a": 1}] * 2
    assert orch._as_memories({"items": [{"i": 1}]}) == [{"i": 1}]
    assert orch._as_memories(None) == [] and orch._as_memories("x") == []


def test_reminder_text():
    assert orch._reminder_text({"text": "süt al"}) == ("Hatırlatma: süt al", "Hatırlatma: süt al")
    assert orch._reminder_text({"title": "T", "spoken": "S"}) == ("Hatırlatma: T", "S")
    assert orch._reminder_text("düz") == ("Hatırlatma: düz", "Hatırlatma: düz")
    assert orch._reminder_text({}) == ("", "") and orch._reminder_text(None) == ("", "")


def test_normalise_result():
    t = tool("browser.read")
    assert orch._normalise_result(t, {"ok": False, "error": "e"}) == {"ok": False, "error": "e"}
    assert orch._normalise_result(t, {"elements": []}) == {"ok": True, "data": {"elements": []}}
    bad = orch._normalise_result(t, {"foo": 1})
    assert bad["ok"] is False and "browser.read" in bad["error"]


def test_frame_id_and_error_text():
    assert orch._frame_id({"id": " r_1 "}) == "r_1"
    assert orch._frame_id({"request_id": "r_2"}) == "r_2"
    assert orch._frame_id({}) is None and orch._frame_id({"id": "  "}) is None
    assert orch._error_text(ValueError("x")) == "x"
    assert orch._error_text(KeyError()) == "KeyError" or orch._error_text(KeyError())
    assert orch._error_text(RuntimeError()) == "RuntimeError"


def test_new_request_id():
    a = orch._new_request_id()
    assert a.startswith("r_") and a != orch._new_request_id()


async def test_unknown_frame_does_not_crash():
    sent = []

    async def send(frame):
        sent.append(frame)

    o = orch.Orchestrator(send)
    await o.handle_frame({"type": "hello"})
    await o.handle_frame({"type": "bogus"})
    await o.handle_frame({"type": "confirm", "call_id": "nope", "approved": "false"})
    await o.handle_frame({"type": "tool_result", "call_id": "nope", "ok": True})
    await o.close()
