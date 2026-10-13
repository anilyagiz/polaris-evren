"""Reasoning agents: router, planner, executor, judge, responder (owner: K3).

Submodules are imported lazily by the orchestrator so that a single missing file
never blocks the whole backend.
"""

from __future__ import annotations

import logging

# ---------------------------------------------------------------------------
# openai 2.x compatibility — must run before any submodule imports `..llm`
# ---------------------------------------------------------------------------
# `backend/llm.py:17` (frozen) does `from openai import APIError, AsyncOpenAI,
# AsyncTimeout`, but openai >= 2.0 no longer exports `AsyncTimeout` (only
# `APITimeoutError`).  That single import used to take the whole chain down
# (llm -> agents.base -> router/planner/executor/judge/responder ->
# orchestrator), which made `backend.main` silently swap /ws for the echo stub.
#
# Every agent module imports this package first, so the alias is installed here.
# It mirrors the documented workaround in cli.py:42-46 and is a harmless no-op
# as soon as llm.py is fixed upstream.
logger = logging.getLogger(__name__)

try:  # pragma: no branch - openai is a declared dependency (INTERFACES.md §2)
    import openai as _openai
except ImportError:  # pragma: no cover - dependency missing: llm.py reports it
    _openai = None  # type: ignore[assignment]

if _openai is not None and not hasattr(_openai, "AsyncTimeout"):
    _openai.AsyncTimeout = _openai.APITimeoutError  # type: ignore[attr-defined]
    logger.info(
        "openai %s: 'AsyncTimeout' yok; APITimeoutError takma adı eklendi (llm.py:17 uyumu)",
        getattr(_openai, "__version__", "?"),
    )

__all__: list[str] = []
