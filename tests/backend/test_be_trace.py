import json
import sqlite3

import pytest

from backend.trace import JudgeScores, TraceStep, TraceStore, make_emitter, make_step


def test_trace_step_wire_shape():
    step = make_step("executor", "Tıkla", "neden", evidence=["ref:3"],
                     tool={"name": "browser.click", "args": {"ref": 3}}, latency_ms=12, tokens=9)
    wire = step.to_wire()
    assert set(wire) == {"agent", "decision", "why", "evidence", "confidence", "tool", "scores",
                         "verdict", "latency_ms", "tokens", "ts"}
    assert wire["tool"] == {"name": "browser.click", "args": {"ref": 3}}
    assert wire["confidence"] is None and wire["scores"] is None
    assert "T" in wire["ts"]
    ev = step.to_event("r_1")
    assert ev["type"] == "trace" and ev["request_id"] == "r_1" and ev["step"] == wire
    json.dumps(ev)


def test_invalid_agent_and_confidence_rejected():
    with pytest.raises(Exception):
        TraceStep(agent="bogus", decision="d", why="w")
    with pytest.raises(Exception):
        make_step("router", "d", "w", confidence=1.5)


def test_make_step_discards_bad_scores_dict():
    step = make_step("judge", "d", "w", scores={"correct": 99, "complete": 1, "safe": 1}, verdict="pass")
    assert step.scores is None


def test_scores_range():
    with pytest.raises(Exception):
        JudgeScores(correct=0, complete=3, safe=3)


async def test_store_save_and_history(tmp_path):
    store = TraceStore(str(tmp_path / "t.db"))
    await store.init()
    s1 = make_step("router", "Route: chat", "selam", confidence=0.9, latency_ms=3)
    s2 = make_step("judge", "Değerlendirme", "ok", scores={"correct": 5, "complete": 4, "safe": 5}, verdict="pass")
    s3 = make_step("executor", "x", "y", tool={"name": "browser.click", "args": {"ref": 1}}, evidence=["url:https://a"])
    for s in (s1, s2, s3):
        await store.save("r_a", s)
    await store.save("r_b", s1)
    rows = await store.history("r_a")
    assert [r["agent"] for r in rows] == ["router", "judge", "executor"]
    assert json.loads(rows[1]["scores"]) == {"correct": 5, "complete": 4, "safe": 5}
    assert rows[1]["verdict"] == "pass"
    assert rows[2]["tool_name"] == "browser.click" and json.loads(rows[2]["tool_args"]) == {"ref": 1}
    assert json.loads(rows[2]["evidence"]) == ["url:https://a"]
    assert len(await store.history("r_b")) == 1
    assert len(await store.history("r_a", limit=2)) == 2


async def test_save_lazy_init_and_never_raises(tmp_path, monkeypatch):
    db = tmp_path / "lazy.db"
    store = TraceStore(str(db))
    await store.save("r", make_step("system", "d", "w"))
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from traces").fetchone()[0] == 1

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk")

    monkeypatch.setattr(store, "_save_sync", boom)
    await store.save("r", make_step("system", "d", "w"))  # must not raise


async def test_emitter_sends_then_persists_and_survives_send_failure(tmp_path):
    store = TraceStore(str(tmp_path / "e.db"))
    sent = []

    async def send(frame):
        sent.append(frame)

    emit = make_emitter(send, store, "r_9")
    step = make_step("planner", "plan", "why")
    assert await emit(step) is step
    assert sent[0]["type"] == "trace" and sent[0]["request_id"] == "r_9"
    assert len(await store.history("r_9")) == 1

    async def bad_send(frame):
        raise RuntimeError("socket closed")

    emit2 = make_emitter(bad_send, store, "r_10")
    await emit2(step)
    assert len(await store.history("r_10")) == 1
