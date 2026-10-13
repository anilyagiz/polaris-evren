"""Suite-wide isolation: never write conversation turns or memories into the
real backend/data/assistant.db, and keep the background memory extractor off
(it would consume scripted fake-LLM responses).  Tests that exercise the
extractor turn it back on with ``monkeypatch.setenv("MEMORY_EXTRACT", "1")``.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_assistant_db(monkeypatch, tmp_path_factory):
    import os

    if not os.environ.get("ASSISTANT_DB"):
        monkeypatch.setenv("ASSISTANT_DB", str(tmp_path_factory.mktemp("db") / "assistant.db"))
    monkeypatch.setenv("MEMORY_EXTRACT", "0")
