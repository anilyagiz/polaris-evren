"""Conversation history (short-term) + long-term memory extraction."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend import conversation, memory_extract
from backend import orchestrator as orch
from backend.agents.base import CONVERSATION_BLOCK, REQUEST_MEMORIES, build_context_block
from backend.llm import Usage
from backend.tools import memory, registry
from backend.trace import TraceStore


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_DB", str(tmp_path / "conv.db"))
    monkeypatch.delenv("CONVERSATION_TURNS", raising=False)


# ---------------------------------------------------------------------------
# fake LLM: routes everything to `chat`, answers from a script
# ---------------------------------------------------------------------------
class FakeClient:
    configured = True

    def __init__(self, facts: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []  # (role, system)
        self.facts = facts or []

    @staticmethod
    def role_of(system: str) -> str:
        if system.startswith("# Hafıza çıkarıcı"):
            return "extractor"
        if "yönlendiricisin" in system[:600]:
            return "router"
        return "responder"

    async def chat_json(self, system: str, messages: list[dict[str, Any]], temperature: float = 0.0):
        role = self.role_of(system)
        user = messages[-1]["content"]
        self.calls.append((role, system))
        usage = Usage(model="fake", latency_ms=1)
        if role == "router":
            return {"route": "chat", "confidence": 0.9, "why": "sohbet"}, usage
        if role == "extractor":
            return {"facts": list(self.facts)}, usage
        goal = next((line for line in user.splitlines() if line.startswith("Hedef:")), "")
        return {"answer": f"Cevap ({goal})", "spoken": "Tamam."}, usage

    def systems(self, role: str) -> list[str]:
        return [system for r, system in self.calls if r == role]


def make_orch(tmp_path, client):
    sent: list[dict[str, Any]] = []

    async def send(frame):
        sent.append(frame)

    o = orch.Orchestrator(send, store=TraceStore(str(tmp_path / "t.db")), client=client)
    o._register_tools()
    return o, sent


async def ask(o, text: str, rid: str) -> None:
    await o.handle_frame({"type": "user_msg", "id": rid, "text": text, "source": "text", "tab": None})
    await o.wait_idle(timeout=20)


# ---------------------------------------------------------------------------
# conversation module
# ---------------------------------------------------------------------------
def test_append_load_and_limit():
    sid = conversation.new_session_id()
    assert conversation.valid_session_id(sid)
    for i in range(15):
        conversation.append_turn_sync(sid, "user", f"soru {i}", f"r{i}")
        conversation.append_turn_sync(sid, "assistant", f"cevap {i}", f"r{i}")
    conversation.append_turn_sync("başka", "user", "x")  # invalid id: ignored
    turns = conversation.recent_turns_sync(sid, 4)
    assert [t["text"] for t in turns] == ["soru 13", "cevap 13", "soru 14", "cevap 14"]
    assert turns[0]["role"] == "user" and turns[0]["request_id"] == "r13" and turns[0]["ts"]
    assert conversation.latest_session_sync()["session_id"] == sid
    with pytest.raises(ValueError):
        conversation.append_turn_sync(sid, "system", "x")


def test_turns_limit_env(monkeypatch):
    assert conversation.turns_limit() == conversation.DEFAULT_TURNS
    monkeypatch.setenv("CONVERSATION_TURNS", "4")
    assert conversation.turns_limit() == 4
    monkeypatch.setenv("CONVERSATION_TURNS", "abc")
    assert conversation.turns_limit() == conversation.DEFAULT_TURNS


@pytest.mark.parametrize("value", ["", "a b", "x" * 65, "ü", None, 5, "../x"])
def test_invalid_session_ids(value):
    assert conversation.valid_session_id(value) is None


def test_render_clips_each_turn_and_caps_total():
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"{i} " + "a" * 900} for i in range(20)]
    block = conversation.render_history(turns)
    assert block.startswith(conversation.HISTORY_HEADER)
    assert len(block) <= conversation.TOTAL_CAP + len(conversation.HISTORY_HEADER) + len(conversation.HISTORY_INSTRUCTION) + 4
    lines = [line for line in block.splitlines() if line.startswith("- ")]
    assert all(len(line) <= conversation.TURN_CLIP + 20 for line in lines)
    # The newest turn survives the cap.
    assert lines[-1].startswith("- Asistan: 19 ")
    assert conversation.render_history([]) == ""


def test_context_block_includes_history_and_request_memories():
    token = CONVERSATION_BLOCK.set("## Önceki konuşma\n- Kullanıcı: Ankara'nın nüfusu kaç?")
    token2 = REQUEST_MEMORIES.set(({"id": 7, "text": "Kullanıcının adı Anıl."},))
    try:
        block = build_context_block()
        assert "Ankara'nın nüfusu" in block and "Kullanıcının adı Anıl." in block
        assert "Önceki konuşma" not in build_context_block(include_history=False)
    finally:
        CONVERSATION_BLOCK.reset(token)
        REQUEST_MEMORIES.reset(token2)
    assert "Önceki konuşma" not in build_context_block()


# ---------------------------------------------------------------------------
# session events
# ---------------------------------------------------------------------------
async def test_hello_without_session_generates_one(tmp_path):
    o, sent = make_orch(tmp_path, FakeClient())
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    assert sent[-1]["type"] == "session" and sent[-1]["turns"] == []
    assert conversation.valid_session_id(sent[-1]["session_id"])
    assert o.session_id == sent[-1]["session_id"]


async def test_hello_with_session_restores_turns_and_invalid_id_is_replaced(tmp_path):
    conversation.append_turn_sync("s_eski", "user", "Merhaba", "r1")
    conversation.append_turn_sync("s_eski", "assistant", "Selam!", "r1")
    o, sent = make_orch(tmp_path, FakeClient())
    await o.handle_frame({"type": "hello", "client": "desktop", "version": "0.1", "session_id": "s_eski"})
    event = sent[-1]
    assert event["session_id"] == "s_eski"
    assert [(t["role"], t["text"]) for t in event["turns"]] == [("user", "Merhaba"), ("assistant", "Selam!")]
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1", "session_id": "kötü id"})
    assert sent[-1]["session_id"] != "kötü id" and sent[-1]["turns"] == []


async def test_new_session_resets_history(tmp_path):
    client = FakeClient()
    o, sent = make_orch(tmp_path, client)
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    first = o.session_id
    await ask(o, "Ankara'nın nüfusu kaç?", "r_1")
    await o.handle_frame({"type": "new_session"})
    assert sent[-1]["type"] == "session" and sent[-1]["turns"] == []
    assert o.session_id != first
    await ask(o, "peki İzmir'in?", "r_2")
    assert "Kullanıcı: Ankara" not in client.systems("router")[-1]
    await o.close()


# ---------------------------------------------------------------------------
# follow-ups reach the agents
# ---------------------------------------------------------------------------
async def test_follow_up_context_in_router_and_responder(tmp_path):
    client = FakeClient()
    o, sent = make_orch(tmp_path, client)
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    await ask(o, "Ankara'nın nüfusu kaç?", "r_1")
    assert "- Kullanıcı: " not in client.systems("router")[0]
    await ask(o, "peki İzmir'in?", "r_2")
    router_system = client.systems("router")[-1]
    assert "## Önceki konuşma" in router_system
    assert "Kullanıcı: Ankara'nın nüfusu kaç?" in router_system
    assert "Asistan: Cevap (Hedef: Ankara'nın nüfusu kaç?)" in router_system
    assert "peki ya" in router_system  # follow-up instruction
    assert "## Önceki konuşma" in client.systems("responder")[-1]
    finals = [f for f in sent if f["type"] == "final"]
    assert len(finals) == 2
    turns = conversation.recent_turns_sync(o.session_id, 10)
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[2]["text"] == "peki İzmir'in?" and turns[2]["request_id"] == "r_2"
    # Reconnect with the same session: history is restored.
    o2, sent2 = make_orch(tmp_path, client)
    await o2.handle_frame({"type": "hello", "client": "extension", "version": "0.1", "session_id": o.session_id})
    assert len(sent2[-1]["turns"]) == 4
    await o.close()
    await o2.close()


async def test_planner_and_executor_get_history(tmp_path):
    from backend.agents.executor import ExecutorAgent

    token = CONVERSATION_BLOCK.set("## Önceki konuşma\n- Kullanıcı: ilk soru")
    try:
        assert "ilk soru" in ExecutorAgent(FakeClient()).context_block("web_search")
    finally:
        CONVERSATION_BLOCK.reset(token)


async def test_failed_request_stores_user_turn_and_note(tmp_path):
    client = FakeClient()
    client.configured = False
    o, sent = make_orch(tmp_path, client)
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    await ask(o, "Bir şey sor", "r_err")
    assert any(f["type"] == "error" for f in sent)
    turns = conversation.recent_turns_sync(o.session_id, 10)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "Bir şey sor" and turns[1]["text"].startswith("(")


async def test_cancelled_request_stores_note(tmp_path):
    class SlowClient(FakeClient):
        async def chat_json(self, system, messages, temperature=0.0):
            await asyncio.sleep(30)

    o, _sent = make_orch(tmp_path, SlowClient())
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    await o.handle_frame({"type": "user_msg", "id": "r_c", "text": "uzun iş", "tab": None})
    await asyncio.sleep(0.3)
    await o.handle_frame({"type": "cancel", "id": "r_c"})
    await o.wait_idle(timeout=5)
    turns = conversation.recent_turns_sync(o.session_id, 10)
    assert turns[0]["text"] == "uzun iş"
    assert "iptal" in turns[1]["text"]


# ---------------------------------------------------------------------------
# long-term memory extraction
# ---------------------------------------------------------------------------
async def test_extraction_stores_facts_filters_secrets_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACT", "1")
    client = FakeClient(
        facts=[
            "Kullanıcının adı (adım) Anıl.",
            "Kullanıcının şifresi 1234.",
            "Kullanıcının kart numarası 4111 1111 1111 1111.",
            "Kullanıcının TC kimlik numarası 12345678901.",
        ]
    )
    o, _sent = make_orch(tmp_path, client)
    await o.handle_frame({"type": "hello", "client": "extension", "version": "0.1"})
    await ask(o, "Benim adım Anıl, hatırla", "r_1")
    await o.wait_background()
    facts = await memory.profile_facts()
    assert [f["text"] for f in facts] == ["Kullanıcının adı (adım) Anıl."]
    # Same fact again: deduplicated.
    await ask(o, "Benim adım Anıl demiştim", "r_2")
    await o.wait_background()
    assert len(await memory.profile_facts()) == 1
    # New session: the profile fact reaches the router and the responder.
    await o.handle_frame({"type": "new_session"})
    await ask(o, "Benim adım ne?", "r_3")
    assert "Kullanıcının adı (adım) Anıl." in client.systems("router")[-1]
    assert "Kullanıcının adı (adım) Anıl." in client.systems("responder")[-1]
    await o.close()


async def test_extraction_skipped_for_trivial_messages(monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACT", "1")
    client = FakeClient(facts=["x"])
    assert await memory_extract.extract_and_store(client, "selam") == []
    assert await memory_extract.extract_and_store(client, "şifrem 12345 bunu hatırla") == []
    assert client.calls == []
    monkeypatch.setenv("MEMORY_EXTRACT", "0")
    assert await memory_extract.extract_and_store(client, "Kahveyi şekersiz içerim") == []
    assert client.calls == []


async def test_extraction_survives_llm_failure(monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACT", "1")

    class Broken(FakeClient):
        async def chat_json(self, *a, **k):
            raise RuntimeError("down")

    assert await memory_extract.extract_and_store(Broken(), "Kahveyi şekersiz içerim") == []


@pytest.mark.parametrize(
    "text",
    [
        "Şifrem abc123",
        "parolam: kedi",
        "kartım 4111-1111-1111-1111",
        "TC kimlik no 12345678901",
        "API key sk-abcdefghijklmnop1234",
        "IBAN TR12 0006 1005 1978 6457 8413 26",
    ],
)
def test_sensitive_detection(text):
    assert memory.is_sensitive(text)


def test_not_sensitive():
    for text in ("Kullanıcının adı Anıl.", "Kahveyi şekersiz içer.", "Evim Kadıköy'de.", "Telefonu 05321234567"):
        assert not memory.is_sensitive(text)


async def test_dedupe_ignores_subject_and_filler_words():
    tool = registry.get("memory.save")
    first = await tool.handler({"text": "Evi Kadıköy'de"}, None)
    assert first["ok"] and not first.get("duplicate")
    assert await memory.save_fact("Kullanıcının evi Kadıköy'de.") is None
    again = await tool.handler({"text": "Kullanıcının evi Kadıköy'de."}, None)
    assert again == {"ok": True, "id": first["id"], "duplicate": True}
    assert await memory.save_fact("Kullanıcı kahveyi hep şekersiz içer.") is not None
    assert await memory.save_fact("Kahveyi şekersiz içer") is None
    assert await memory.save_fact("Annesinin evi Ankara'da.") is not None


async def test_memory_save_tool_refuses_secrets():
    tool = registry.get("memory.save")
    result = await tool.handler({"text": "Banka şifrem 4455"}, None)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# memory.forget
# ---------------------------------------------------------------------------
async def test_memory_forget_by_query_id_and_all():
    tool = registry.get("memory.forget")
    assert tool is not None and tool.risk == "confirm"
    assert "memory.forget" in [t.name for t in registry.list_for("personal")]
    a = await memory.save("Kullanıcı kahveyi şekersiz içer.", ["profil"])
    b = await memory.save("Kullanıcının evi Kadıköy'de.", ["profil"])
    c = await memory.save("Annemin doğum günü 3 Mayıs.")
    res = await tool.handler({"query": "kahveyi"}, None)
    assert res["ok"] and res["deleted"] == 1 and res["items"][0]["id"] == a
    res = await tool.handler({"id": b}, None)
    assert res["ok"] and res["items"][0]["id"] == b
    assert (await tool.handler({"query": "uzay gemisi"}, None))["ok"] is False
    assert (await tool.handler({}, None))["ok"] is False
    res = await tool.handler({"all": True}, None)
    assert res["ok"] and [i["id"] for i in res["items"]] == [c]
    assert await memory.search("doğum") == []


# ---------------------------------------------------------------------------
# GET /session/latest
# ---------------------------------------------------------------------------
def test_session_latest_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    client = TestClient(main.app)
    assert client.get("/session/latest").status_code == 403  # "testclient" is not loopback
    monkeypatch.setattr(main, "is_loopback", lambda host: True)
    assert client.get("/session/latest").json() == {"session_id": None}
    conversation.append_turn_sync("s_a", "user", "bir")
    conversation.append_turn_sync("s_b", "user", "iki")
    body = client.get("/session/latest").json()
    assert body["session_id"] == "s_b" and body["turns"] == 1
