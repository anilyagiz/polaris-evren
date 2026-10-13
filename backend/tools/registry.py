"""TEMPORARY SCAFFOLD — this file is owned by Person 3 (K3).

It matches the frozen contract in ORTAK BAĞLAM §7 exactly, so K3's real
registry can drop in without touching the K5 modules:

    Tool(name, description, parameters, risk, handler)
    ToolContext(request_id, bridge, tab, emit_trace)
    register(tool)

``get()`` and ``all_tools()`` are extras used by scripts/test_tools.py; if the
real registry does not provide them, update that import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

Risk = Literal["safe", "confirm"]
Handler = Callable[[dict, "ToolContext"], Awaitable[dict]]


async def _noop_trace(step: dict) -> None:
    """Default emit_trace: does nothing when a tool runs outside the orchestrator."""
    return None


@dataclass
class Tool:
    name: str                 # "web.search" — K3 renders it as "web__search" for the LLM
    description: str          # Turkish, written for the LLM
    parameters: dict          # JSON Schema (object)
    risk: Risk                # "safe" | "confirm"
    handler: Handler          # async (args, ctx) -> {"ok": ...}


@dataclass
class ToolContext:
    request_id: str = "local"
    bridge: Any = None               # K3's BrowserBridge (browser.* tools)
    tab: dict | None = None
    emit_trace: Callable[[dict], Awaitable[None]] = _noop_trace


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """Register a tool; the same name cannot be registered twice."""
    if tool.name in _REGISTRY:
        raise ValueError(f"tool already registered: {tool.name}")
    _REGISTRY[tool.name] = tool


def get(name: str) -> Tool | None:
    """Return a registered tool, or None."""
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    """Return every registered tool."""
    return list(_REGISTRY.values())
