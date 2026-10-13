"""LLM_FALLBACK_MODEL and text-written tool calls (Gemma via EVREN)."""

from __future__ import annotations

from backend.llm import _text_tool_calls

TOOLS = [
    {"type": "function", "function": {"name": "reminders__add"}},
    {"type": "function", "function": {"name": "finish"}},
]


def test_text_tool_call_in_code_fence() -> None:
    text = '```json\n{"name": "reminders__add", "arguments": {"text": "x", "due_iso": "2026-10-14T10:00:00"}}\n```'
    calls = _text_tool_calls(text, TOOLS)
    assert [c.name for c in calls] == ["reminders__add"]
    assert calls[0].args["text"] == "x"


def test_text_tool_call_dotted_name_and_string_args() -> None:
    calls = _text_tool_calls('{"name": "reminders.add", "arguments": "{\\"text\\": \\"y\\"}"}', TOOLS)
    assert calls and calls[0].name == "reminders__add" and calls[0].args == {"text": "y"}


def test_plain_text_is_not_a_tool_call() -> None:
    assert _text_tool_calls("Merhaba, hatırlatıcı eklendi.", TOOLS) == []
    assert _text_tool_calls('{"name": "unknown_tool", "arguments": {}}', TOOLS) == []
