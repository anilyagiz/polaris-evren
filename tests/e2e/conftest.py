"""pytest wrappers around the scripts/e2e scenarios (one test per scenario)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e.harness import Stack  # noqa: E402


@pytest.fixture(scope="session")
def e2e_stack():
    stack = Stack()
    yield stack
    stack.stop()
