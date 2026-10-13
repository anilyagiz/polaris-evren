"""GEÇİCİ TASLAK — Kişi 3'ün tools/registry.py dosyası geldiğinde bunu silin/yerine geçsin.

Sözleşme (ORTAK BAĞLAM §7) ile birebir aynı imzayı kullanır:
    Tool(name, description, parameters, risk, handler)
    ToolContext(request_id, bridge, tab, emit_trace)
    registry.register(tool)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

Risk = Literal["safe", "confirm"]
Handler = Callable[[dict, "ToolContext"], Awaitable[dict]]


async def _noop_trace(step: dict) -> None:
    """Varsayılan emit_trace: tek başına çalışan testlerde hiçbir şey yapmaz."""
    return None


@dataclass
class Tool:
    name: str                 # "web.search" — LLM'e giderken "web__search" olur (K3 çevirir)
    description: str          # Türkçe, LLM için
    parameters: dict          # JSON Schema (object)
    risk: Risk                # "safe" | "confirm"
    handler: Handler          # async (args, ctx) -> {"ok": ... }


@dataclass
class ToolContext:
    request_id: str = "local"
    bridge: Any = None               # K3'ün BrowserBridge'i (browser.* tool'larında)
    tab: dict | None = None
    emit_trace: Callable[[dict], Awaitable[None]] = _noop_trace


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """Tool'u kaydeder; aynı ad iki kez kaydedilemez."""
    if tool.name in _REGISTRY:
        raise ValueError(f"tool already registered: {tool.name}")
    _REGISTRY[tool.name] = tool


def get(name: str) -> Tool | None:
    """Kayıtlı tool'u döndürür (yoksa None)."""
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    """Kayıtlı tüm tool'ları listeler."""
    return list(_REGISTRY.values())
