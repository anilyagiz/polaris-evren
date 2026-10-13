"""Tests for web.search, memory.* and reminders.* (temp DB, mocked httpx)."""
from __future__ import annotations

import asyncio
import importlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend.tools import memory, registry, reminders, search


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("TIMEZONE", "Europe/Istanbul")
    monkeypatch.setenv("BRAVE_API_KEY", "k")


CTX = SimpleNamespace(request_id="t", bridge=None, tab=None, extras={})


def run(coro):
    return asyncio.run(coro)


def call(name, args):
    tool = registry.get(name)
    assert tool is not None, name
    return run(tool.handler(args, CTX))


_REAL_CLIENT = httpx.AsyncClient


def mock_http(monkeypatch, handler):
    real = _REAL_CLIENT
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(search.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


def test_registration_contract():
    spec = {
        "web.search": ({"query"}, {"query", "count"}),
        "memory.save": ({"text"}, {"text", "tags"}),
        "memory.search": ({"query"}, {"query"}),
        "reminders.add": ({"text", "due_iso"}, {"text", "due_iso"}),
        "reminders.list": (set(), set()),
    }
    for name, (required, props) in spec.items():
        tool = registry.get(name)
        assert tool.risk == "safe"
        assert set(tool.parameters.get("required", [])) == required
        assert set(tool.parameters["properties"]) == props


def test_reimport_is_idempotent():
    before = len(registry.all_tools())
    for mod in (search, memory, reminders):
        importlib.reload(mod)
    assert len(registry.all_tools()) == before


def test_search_success_strips_html(monkeypatch):
    seen = {}

    def handler(request):
        seen["token"] = request.headers["X-Subscription-Token"]
        seen["count"] = request.url.params["count"]
        return httpx.Response(200, json={"web": {"results": [
            {"title": "<b>A</b>", "url": "https://a.io", "description": "x <strong>y</strong> &amp; z"},
            {"title": "no url"},
        ]}})

    mock_http(monkeypatch, handler)
    out = call("web.search", {"query": "kedi"})
    assert out == {"ok": True, "results": [{"title": "A", "url": "https://a.io", "snippet": "x y & z"}]}
    assert seen == {"token": "k", "count": "5"}


def test_search_errors(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK", "off")
    out = call("web.search", {"query": "x"})
    assert out["ok"] is False and "BRAVE_API_KEY" in out["error"]
    monkeypatch.delenv("WEB_SEARCH_FALLBACK")

    monkeypatch.setenv("BRAVE_API_KEY", "k")
    mock_http(monkeypatch, lambda r: httpx.Response(429))
    assert "limit" in call("web.search", {"query": "x"})["error"]

    def boom(request):
        raise httpx.ConnectTimeout("t")

    mock_http(monkeypatch, boom)
    assert call("web.search", {"query": "x"})["ok"] is False
    assert call("web.search", {"query": "  "})["ok"] is False
    mock_http(monkeypatch, lambda r: httpx.Response(200, text="not json"))
    assert call("web.search", {"query": "x"})["ok"] is False


def test_search_count_clamped(monkeypatch):
    got = []
    mock_http(monkeypatch, lambda r: got.append(r.url.params["count"]) or httpx.Response(200, json={}))
    call("web.search", {"query": "x", "count": 999})
    call("web.search", {"query": "x", "count": "abc"})
    assert got == ["20", "5"]


def test_turkish_lower():
    assert memory.turkish_lower("İSTANBUL ISPARTA") == "istanbul ısparta"


def test_memory_roundtrip_and_ranking():
    a = call("memory.save", {"text": "Ayşe'nin DOĞUM günü 14 Şubat", "tags": ["kişi"]})
    b = call("memory.save", {"text": "Market listesi: süt, yumurta"})
    assert a["ok"] and b["ok"] and a["id"] != b["id"]
    found = call("memory.search", {"query": "doğum günü"})["items"]
    assert [i["id"] for i in found] == [a["id"]]
    assert call("memory.search", {"query": "DOĞUM GÜNÜ"})["items"]
    assert call("memory.search", {"query": "kişi"})["items"]
    assert call("memory.search", {"query": "ve ile bir"})["items"] == []
    assert call("memory.search", {"query": "100%"})["items"] == []
    assert run(memory.relevant("doğum günü", k=3)) == ["Ayşe'nin DOĞUM günü 14 Şubat"]
    assert run(memory.relevant("")) == []


def test_memory_more_matches_first():
    call("memory.save", {"text": "kahve sever"})
    best = call("memory.save", {"text": "kahve ve çay sever"})["id"]
    items = call("memory.search", {"query": "kahve çay"})["items"]
    assert items[0]["id"] == best and len(items) == 2


def test_memory_errors():
    assert call("memory.save", {"text": " "})["ok"] is False
    assert call("memory.save", {"text": "x", "tags": "a"})["ok"] is False
    assert call("memory.search", {})["ok"] is False


def test_reminders_flow():
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert call("reminders.add", {"text": "a", "due_iso": soon})["ok"]
    items = call("reminders.list", {})["items"]
    assert len(items) == 1 and items[0]["due_iso"].endswith("+03:00")


def test_reminders_naive_is_local_and_validation():
    day = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    assert call("reminders.add", {"text": "a", "due_iso": f"{day}T10:00:00"})["ok"]
    item = call("reminders.list", {})["items"][0]
    assert item["due_iso"].endswith("T10:00:00+03:00")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert "geçmiş" in call("reminders.add", {"text": "a", "due_iso": past})["error"]
    assert call("reminders.add", {"text": "a", "due_iso": "yarın"})["ok"] is False
    assert call("reminders.add", {"text": "", "due_iso": past})["ok"] is False


def test_due_once_and_unmark():
    rid = run(reminders.add("x", datetime.now(timezone.utc) + timedelta(milliseconds=300)))
    assert run(reminders.due()) == []
    time.sleep(1.2)
    first = run(reminders.due())
    assert [r["id"] for r in first] == [rid] and "+03:00" in first[0]["due_iso"]
    assert run(reminders.due()) == []
    assert call("reminders.list", {})["items"] == []
    run(reminders.unmark([rid]))
    assert len(run(reminders.due())) == 1


def test_watcher_delivers_and_retries():
    run(reminders.add("w", datetime.now(timezone.utc) + timedelta(milliseconds=100)))
    got, attempts = [], []

    async def send(rec):
        attempts.append(rec["id"])
        if len(attempts) == 1:
            raise RuntimeError("socket closed")
        got.append(rec["text"])

    async def go():
        task = asyncio.create_task(reminders.start_watcher(send, interval=0.3))
        await asyncio.sleep(2.0)
        task.cancel()

    run(go())
    assert got == ["w"] and len(attempts) == 2


def test_db_wal_and_autocreate(tmp_path, monkeypatch):
    from backend import db

    monkeypatch.setenv("ASSISTANT_DB", str(tmp_path / "sub" / "x.db"))
    with db.session() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    call("memory.save", {"text": "ilk"})
    assert (tmp_path / "sub" / "x.db").exists()


def test_search_keyless_duckduckgo_fallback(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "")
    monkeypatch.delenv("WEB_SEARCH_FALLBACK", raising=False)
    page = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftr.wikipedia.org%2Fwiki%2FBrave&amp;rut=x">'
        "<b>Brave</b> - Vikipedi</a> ... "
        '<a class="result__snippet" href="#">Brave bir <b>tarayıcı</b>dır.</a>'
    )
    mock_http(monkeypatch, lambda r: httpx.Response(200, text=page))
    out = call("web.search", {"query": "brave"})
    assert out["ok"] is True
    assert out["results"][0] == {
        "title": "Brave - Vikipedi",
        "url": "https://tr.wikipedia.org/wiki/Brave",
        "snippet": "Brave bir tarayıcıdır.",
    }
