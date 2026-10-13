"""Shared fixtures: repo root on sys.path, isolated settings, fake LLM pieces."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    """Set env vars, clear the lru_cache on get_settings, restore afterwards."""
    from backend import config

    for key in (
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LLM_MODEL", "TIMEZONE", "CUA_ENABLED",
        "PORT", "TOOL_TIMEOUT_S", "MAX_TOOL_CALLS", "LLM_TIMEOUT_S", "DB_PATH",
        "DESKTOP_PARSER", "REQUEST_BUDGET_S", "CONFIRM_TIMEOUT_S",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "settings.db"))

    def apply(**env: str):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        config.get_settings.cache_clear()
        return config.get_settings()

    config.get_settings.cache_clear()
    yield apply
    config.get_settings.cache_clear()


def make_response(content, *, tool_calls=None, total=7, finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        model="fake-model",
        choices=[SimpleNamespace(message=msg, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=total),
    )


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_llm():
    """Factory: fake_llm(script) -> (LLMClient wired to a scripted SDK, completions)."""
    from backend.llm import LLMClient

    def build(script):
        client = LLMClient(api_key="k", base_url="http://x", model="m", timeout_s=1)
        comps = FakeCompletions(script)
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=comps))
        return client, comps

    return build


class ScriptedClient:
    """Stand-in LLMClient for agents: chat_json returns/raises scripted items."""

    def __init__(self, *items):
        self.items = list(items)

    async def chat_json(self, system, messages, temperature=0.0):
        from backend.llm import Usage

        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item, Usage(model="m", total_tokens=5, latency_ms=1)


@pytest.fixture
def scripted():
    return ScriptedClient


@pytest.fixture
def emitted():
    steps = []

    async def emit(step):
        steps.append(step)
        return step

    emit.steps = steps
    return emit
