import asyncio
from types import SimpleNamespace as NS

import pytest

from backend import llm
from backend.llm import LLMClient, LLMError, _loads_lenient
from tests.backend.conftest import make_response


def test_loads_lenient_variants():
    assert _loads_lenient('{"a": 1}') == {"a": 1}
    assert _loads_lenient('```json\n{"a": 2}\n```') == {"a": 2}
    assert _loads_lenient('Tabii: {"a": 3} umarım olur') == {"a": 3}
    for bad in ("[1,2]", "düz metin", ""):
        with pytest.raises(Exception):
            _loads_lenient(bad)


async def test_chat_json_ok(fake_llm):
    client, comps = fake_llm([make_response('{"route": "chat"}')])
    data, usage = await client.chat_json("sys", [{"role": "user", "content": "hi"}])
    assert data == {"route": "chat"}
    assert usage.tokens == 7 and usage.attempts == 1
    assert comps.calls[0]["response_format"] == {"type": "json_object"}
    assert comps.calls[0]["messages"][0] == {"role": "system", "content": "sys"}


async def test_chat_json_retries_once_then_succeeds(fake_llm):
    client, comps = fake_llm([make_response("bozuk"), make_response('{"ok": true}')])
    data, usage = await client.chat_json("s", [{"role": "user", "content": "x"}])
    assert data == {"ok": True} and usage.attempts == 2
    retry_msgs = comps.calls[1]["messages"]
    assert retry_msgs[-1]["role"] == "user" and "JSON" in retry_msgs[-1]["content"]
    assert retry_msgs[-2] == {"role": "assistant", "content": "bozuk"}


async def test_chat_json_two_failures_raise(fake_llm):
    client, comps = fake_llm([make_response("a"), make_response("b")])
    with pytest.raises(LLMError):
        await client.chat_json("s", [])
    assert len(comps.calls) == 2


async def test_chat_json_empty_choices_is_llmerror(fake_llm):
    resp = make_response("{}")
    resp.choices = []
    client, _ = fake_llm([resp])
    with pytest.raises(LLMError):
        await client.chat_json("s", [])


async def test_unconfigured_raises_llmerror():
    client = LLMClient(api_key="", base_url="http://x", model="m")
    assert not client.configured
    with pytest.raises(LLMError):
        await client.chat_json("s", [])


def test_config_problem_checks():
    assert LLMClient(api_key="k", base_url="ftp://x", model="m").config_problem()
    assert LLMClient(api_key="k", base_url="https://x", model="m").config_problem() is None


async def test_transient_error_backoff_then_success(fake_llm, monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    client, comps = fake_llm([asyncio.TimeoutError(), make_response('{"a":1}')])
    data, _ = await client.chat_json("s", [])
    assert data == {"a": 1} and len(sleeps) == 1


async def test_timeouts_exhaust_into_llmerror(fake_llm, monkeypatch):
    async def fake_sleep(d):
        pass

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    client, comps = fake_llm([asyncio.TimeoutError()] * 3)
    with pytest.raises(LLMError) as ei:
        await client.chat_json("s", [])
    assert "zaman aşımı" in str(ei.value)
    assert len(comps.calls) == 3


async def test_chat_tools_parses_calls_and_bad_args(fake_llm):
    good = NS(id="t1", function=NS(name="browser__click", arguments='{"ref": 3}'))
    bad = NS(id="t2", function=NS(name="browser__type", arguments="{oops"))
    nonobj = NS(id="t3", function=NS(name="browser__read", arguments="[1]"))
    client, comps = fake_llm([make_response("", tool_calls=[good, bad, nonobj])])
    msg, usage = await client.chat_tools("s", [], tools=[{"type": "function"}])
    assert [c.name for c in msg.tool_calls] == ["browser__click", "browser__type", "browser__read"]
    assert msg.tool_calls[0].args == {"ref": 3} and msg.tool_calls[0].parse_error is None
    assert msg.tool_calls[1].parse_error and msg.tool_calls[1].args == {}
    assert msg.tool_calls[2].parse_error
    assert comps.calls[0]["tool_choice"] == "auto"


async def test_chat_tools_no_tools_omits_tool_choice(fake_llm):
    client, comps = fake_llm([make_response("merhaba")])
    msg, _ = await client.chat_tools("s", [], tools=[])
    assert msg.content == "merhaba" and msg.tool_calls == []
    assert "tools" not in comps.calls[0] and "tool_choice" not in comps.calls[0]


def test_set_llm_singleton():
    fake = LLMClient(api_key="k", base_url="http://x", model="m")
    llm.set_llm(fake)
    try:
        assert llm.get_llm() is fake
    finally:
        llm.set_llm(None)
