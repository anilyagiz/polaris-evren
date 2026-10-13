"""One pytest test per E2E scenario.  Slow (spawns real backend processes):

    POLARIS_E2E=1 pytest tests/e2e -q          # everything
    POLARIS_E2E=1 pytest tests/e2e -k risky    # a subset
"""

from __future__ import annotations

import asyncio
import os

import pytest

from scripts.e2e.scenarios import SCENARIOS, run_scenario
from scripts.e2e.schema import validate_event

RUN_E2E = os.environ.get("POLARIS_E2E", "").lower() in ("1", "true", "yes")


@pytest.mark.skipif(not RUN_E2E, reason="slow: spawns real backends; set POLARIS_E2E=1 (or run scripts/e2e/run.py)")
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(e2e_stack, name: str) -> None:
    result = asyncio.run(run_scenario(e2e_stack, name))
    details = "\n".join(result.failures + result.notes)
    assert result.ok, f"{name} failed:\n{details}"


# -- schema self-tests (fast, no processes) ----------------------------------
GOOD_STEP = {
    "agent": "router", "decision": "Route: chat", "why": "selamlama", "evidence": ["ref:21", "url:https://a.b", "shot:/shots/r_1_2.png", "serbest"],
    "confidence": 0.9, "tool": None, "scores": None, "verdict": None, "latency_ms": 12, "tokens": 5,
    "ts": "2026-10-13T14:02:11+03:00",
}


def test_schema_accepts_valid_frames() -> None:
    assert validate_event({"type": "trace", "request_id": "r_1", "step": GOOD_STEP}) == []
    assert validate_event({"type": "tool_call", "call_id": "c_1", "request_id": "r_1", "name": "browser.click", "args": {"ref": 1}}) == []
    assert validate_event({"type": "need_confirm", "call_id": "c_1", "request_id": "r_1", "summary": "emin misin?", "tool": "browser.click", "args": {}}) == []
    assert validate_event({"type": "final", "request_id": "r_1", "answer": "x", "spoken": "Tamam.", "citations": [{"title": "t", "url": "https://a.b", "ref": 3}]}) == []
    assert validate_event({"type": "notify", "text": "a", "spoken": "b"}) == []
    assert validate_event({"type": "error", "request_id": None, "message": "m"}) == []


@pytest.mark.parametrize(
    "event",
    [
        {"type": "trace", "request_id": "r", "step": {**GOOD_STEP, "agent": "boss"}},
        {"type": "trace", "request_id": "r", "step": {**GOOD_STEP, "confidence": 1.5}},
        {"type": "trace", "request_id": "r", "step": {**GOOD_STEP, "scores": {"correct": 5, "complete": 5, "safe": 5}, "verdict": "pass"}},
        {"type": "trace", "request_id": "r", "step": {**GOOD_STEP, "evidence": ["ref:abc"]}},
        {"type": "trace", "request_id": "r", "step": {k: v for k, v in GOOD_STEP.items() if k != "ts"}},
        {"type": "tool_call", "call_id": "c", "request_id": "r", "name": "web.search", "args": {}},
        {"type": "final", "request_id": "r", "answer": "x", "spoken": "Bir. İki. Üç.", "citations": []},
        {"type": "final", "request_id": "r", "answer": "x", "spoken": "bkz https://a.b", "citations": []},
        {"type": "mystery"},
    ],
)
def test_schema_rejects_invalid_frames(event) -> None:
    assert validate_event(event)
