"""EXPERIMENT ONLY (run.mjs --live --tool-choice-required).

Starts the real backend exactly like `uvicorn backend.main:app`, but forces
`tool_choice="required"` on every LLM call that offers tools (the executor).
Used to prove the proposed fix for the live failure where the real model
writes the tool call as a ```json text block instead of a function call.
Product code is not modified; `finish` is itself a tool, so "required" never
blocks the model from ending the task.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402

from backend import llm  # noqa: E402

_original_create = llm.LLMClient._create


async def _create(self: Any, **kwargs: Any) -> Any:
    if kwargs.get("tools"):
        kwargs["tool_choice"] = "required"
    return await _original_create(self, **kwargs)


llm.LLMClient._create = _create  # type: ignore[method-assign]

from backend.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="info")
