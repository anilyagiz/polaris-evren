"""JSON Schema for every server -> client frame (docs/PROTOCOL.md section 6).

`validate_event(event)` returns a list of human-readable violations (empty =
valid).  It is intentionally strict: the protocol is frozen, so unknown fields,
wrong types and out-of-range values are all reported.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator

_TS = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
_STR = {"type": "string"}
_NULLABLE_STR = {"type": ["string", "null"]}

TRACE_STEP: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "agent", "decision", "why", "evidence", "confidence", "tool",
        "scores", "verdict", "latency_ms", "tokens", "ts",
    ],
    "properties": {
        "agent": {"enum": ["router", "planner", "executor", "judge", "responder", "system"]},
        "decision": _STR,
        "why": _STR,
        "evidence": {"type": "array", "items": _STR},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "tool": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "args"],
                    "properties": {"name": _STR, "args": {"type": "object"}},
                },
            ]
        },
        "scores": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["correct", "complete", "safe"],
                    "properties": {
                        k: {"type": "integer", "minimum": 1, "maximum": 5}
                        for k in ("correct", "complete", "safe")
                    },
                },
            ]
        },
        "verdict": {"enum": ["pass", "revise", "ask_user", None]},
        "latency_ms": {"type": "integer", "minimum": 0},
        "tokens": {"type": ["integer", "null"], "minimum": 0},
        "ts": {"type": "string", "pattern": _TS},
    },
}

_REQ_ID = {"type": "string", "minLength": 1}

EVENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "trace": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "request_id", "step"],
        "properties": {"type": {"const": "trace"}, "request_id": _REQ_ID, "step": TRACE_STEP},
    },
    "tool_call": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "call_id", "request_id", "name", "args"],
        "properties": {
            "type": {"const": "tool_call"},
            "call_id": {"type": "string", "minLength": 1},
            "request_id": _REQ_ID,
            "name": {"type": "string", "pattern": r"^browser\.[a-z_]+$"},
            "args": {"type": "object"},
        },
    },
    "need_confirm": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "call_id", "request_id", "summary", "tool", "args"],
        "properties": {
            "type": {"const": "need_confirm"},
            "call_id": {"type": "string", "minLength": 1},
            "request_id": _REQ_ID,
            "summary": {"type": "string", "minLength": 1},
            "tool": {"type": "string", "pattern": r"^[a-z]+\.[a-z_]+$"},
            "args": {"type": "object"},
        },
    },
    "final": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "request_id", "answer", "spoken", "citations"],
        "properties": {
            "type": {"const": "final"},
            "request_id": _REQ_ID,
            "answer": {"type": "string", "minLength": 1},
            "spoken": {"type": "string", "minLength": 1},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "url"],
                    "properties": {
                        "title": _STR,
                        "url": _STR,
                        "ref": {"type": ["integer", "null"]},
                    },
                },
            },
        },
    },
    "notify": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "text", "spoken"],
        "properties": {
            "type": {"const": "notify"},
            "text": {"type": "string", "minLength": 1},
            "spoken": {"type": "string", "minLength": 1},
        },
    },
    "error": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "request_id", "message"],
        "properties": {
            "type": {"const": "error"},
            "request_id": {"type": ["string", "null"]},
            "message": {"type": "string", "minLength": 1},
        },
    },
}

_VALIDATORS = {name: Draft202012Validator(schema) for name, schema in EVENT_SCHEMAS.items()}

_EVIDENCE_PREFIX = {
    "ref:": re.compile(r"^ref:\d+$"),
    "url:": re.compile(r"^url:https?://\S+$"),
    "shot:": re.compile(r"^shot:/shots/[A-Za-z0-9_.-]+\.png$"),
}
_MD_OR_URL = re.compile(r"https?://|[*_#`>]|\]\(")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _path(err: Any) -> str:
    return "/".join(str(p) for p in err.absolute_path) or "(root)"


def validate_event(event: Any) -> list[str]:
    """Return a list of contract violations for one server -> client frame."""

    if not isinstance(event, dict):
        return [f"frame is not an object: {type(event).__name__}"]
    kind = event.get("type")
    validator = _VALIDATORS.get(kind) if isinstance(kind, str) else None
    if validator is None:
        return [f"unknown server event type: {kind!r}"]

    problems = [f"{kind}: {_path(e)}: {e.message[:160]}" for e in validator.iter_errors(event)]
    if problems:
        return problems

    if kind == "trace":
        step = event["step"]
        if step["agent"] != "judge" and (step["scores"] is not None):
            problems.append(f"trace: scores only allowed for judge (agent={step['agent']})")
        if step["agent"] != "judge" and step["verdict"] is not None:
            problems.append(f"trace: verdict only allowed for judge (agent={step['agent']})")
        if step["agent"] == "judge" and step["scores"] is not None and step["verdict"] is None:
            problems.append("trace: judge step with scores must carry a verdict")
        for item in step["evidence"]:
            for prefix, rx in _EVIDENCE_PREFIX.items():
                if item.startswith(prefix) and not rx.match(item):
                    problems.append(f"trace: malformed evidence {item!r}")
    elif kind == "final":
        spoken = event["spoken"]
        if _MD_OR_URL.search(spoken):
            problems.append(f"final: spoken contains markdown/URL: {spoken[:80]!r}")
        if len([s for s in _SENTENCE_SPLIT.split(spoken.strip()) if s]) > 2:
            problems.append(f"final: spoken has more than 2 sentences: {spoken[:120]!r}")
        for cite in event["citations"]:
            if not cite["url"].startswith(("http://", "https://")):
                problems.append(f"final: citation url not http(s): {cite['url']!r}")
    return [f"{p}" for p in problems]
