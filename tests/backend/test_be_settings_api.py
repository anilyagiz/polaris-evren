"""/settings/llm: panel-driven key/model settings (never touches the real backend/.env)."""
from __future__ import annotations

import httpx
import openai
import pytest

from backend.trace import TraceStore

KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_MODEL", "LLM_THINKING", "BRAVE_API_KEY")
SECRET = "sk-test-SECRETvalue-X45NMo"
EXT = "chrome-extension://" + "a" * 32

ENV_TEXT = (
    "# Polaris ayarları\r\n"
    "DEEPSEEK_API_KEY=old-key-1234567890\r\n"
    "# yorum korunmalı\r\n"
    "DEEPSEEK_BASE_URL=https://api.deepseek.com\r\n"
    "LLM_MODEL=deepseek-chat\r\n"
    "STT_MODEL=small                 # small | base\r\n"
    "TIMEZONE=Europe/Istanbul\r\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import config, llm, main, settings_api

    env_file = tmp_path / ".env"
    env_file.write_bytes(ENV_TEXT.encode("utf-8"))
    monkeypatch.setattr(settings_api, "ENV_PATH", env_file)
    monkeypatch.setattr(settings_api, "ENV_EXAMPLE_PATH", tmp_path / ".env.example")
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-key-1234567890")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setattr(main, "store", TraceStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(llm, "_CLIENT", None)
    config.get_settings.cache_clear()
    yield env_file
    config.get_settings.cache_clear()


def _client(host="127.0.0.1"):
    from fastapi.testclient import TestClient

    from backend import main

    return TestClient(main.app, client=(host, 50000))


def test_get_shape(env):
    data = _client().get("/settings/llm", headers={"Origin": EXT}).json()
    assert [p["id"] for p in data["presets"]] == ["evren", "deepseek", "custom"]
    evren = data["presets"][0]
    assert evren["recommended"] == {"model": "gemma-4-31b", "fallback_model": "deepseek-v4.1-flash", "thinking": "off"}
    assert data["provider"] == "deepseek" and data["model"] == "deepseek-chat"
    assert data["fallback_model"] is None and data["thinking"] == "off"  # LLM_THINKING unset -> off (default)
    assert data["api_key_set"] is True and data["api_key_hint"] == "…7890"
    assert data["brave_key_set"] is False and data["llm_configured"] is True
    assert "old-key-1234567890" not in str(data)


def test_put_writes_preserves_and_applies_live(env):
    from backend import llm

    shared = llm.get_llm()
    c = _client()
    r = c.put(
        "/settings/llm",
        headers={"Origin": "http://localhost:5173"},
        json={
            "base_url": "https://evren-llmapi.ssyz.org.tr/v1",
            "model": "gemma-4-31b",
            "fallback_model": "deepseek-v4.1-flash",
            "thinking": "off",
            "api_key": f"  {SECRET}\n",
            "brave_api_key": "brv-abc",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert SECRET not in r.text and "brv-abc" not in r.text
    assert data["provider"] == "evren" and data["model"] == "gemma-4-31b"
    assert data["fallback_model"] == "deepseek-v4.1-flash" and data["thinking"] == "off"
    assert data["api_key_hint"] == "…5NMo" and data["brave_key_set"] is True

    raw = env.read_bytes().decode("utf-8")
    assert "# Polaris ayarları\r\n" in raw and "# yorum korunmalı\r\n" in raw
    assert "STT_MODEL=small                 # small | base\r\n" in raw
    assert f"DEEPSEEK_API_KEY={SECRET}\r\n" in raw and "old-key" not in raw
    assert "LLM_MODEL=gemma-4-31b\r\n" in raw and "LLM_THINKING=off\r\n" in raw
    assert "LLM_FALLBACK_MODEL=deepseek-v4.1-flash\r\n" in raw and "BRAVE_API_KEY=brv-abc\r\n" in raw
    assert not [p for p in env.parent.iterdir() if p.name.endswith(".tmp")]

    health = c.get("/health").json()
    assert health["llm_model"] == "gemma-4-31b" and health["llm_configured"] is True
    assert health["brave_configured"] is True
    assert shared.model == "gemma-4-31b" and shared.api_key == SECRET
    assert shared.base_url == "https://evren-llmapi.ssyz.org.tr/v1"

    # clears / leave-unchanged semantics
    r = c.put("/settings/llm", json={"fallback_model": "", "api_key": "", "brave_api_key": None})
    data = r.json()
    assert data["fallback_model"] is None and data["api_key_set"] is True and data["brave_key_set"] is False
    raw = env.read_bytes().decode("utf-8")
    assert "LLM_FALLBACK_MODEL=\r\n" in raw and f"DEEPSEEK_API_KEY={SECRET}" in raw and "BRAVE_API_KEY=\r\n" in raw
    r = c.put("/settings/llm", json={"brave_api_key": ""})
    assert r.json()["brave_key_set"] is False


def test_put_creates_env_from_example(env, tmp_path):
    env.unlink()
    (tmp_path / ".env.example").write_text("# örnek\nDEEPSEEK_API_KEY=buraya\nTIMEZONE=Europe/Istanbul\n", encoding="utf-8")
    r = _client().put("/settings/llm", json={"api_key": SECRET, "model": "deepseek-reasoner"})
    assert r.status_code == 200
    raw = env.read_text(encoding="utf-8")
    assert raw.startswith("# örnek\n") and f"DEEPSEEK_API_KEY={SECRET}\n" in raw
    assert "TIMEZONE=Europe/Istanbul" in raw and raw.endswith("LLM_MODEL=deepseek-reasoner\n")


@pytest.mark.parametrize(
    "body, needle",
    [
        ({"base_url": "ftp://x"}, "http"),
        ({"base_url": ""}, "boş"),
        ({"model": ""}, "boş"),
        ({"model": "a b"}, "geçersiz"),
        ({"model": "m" * 200}, "uzun"),
        ({"thinking": "maybe"}, "thinking"),
        ({"api_key": "abc def"}, "boşluk"),
        ({"api_key": "abc\ndef"}, "boşluk"),
        ({"brave_api_key": "x#y"}, "geçersiz"),
        ({"api_key": 123}, "metin"),
    ],
)
def test_put_invalid_422(env, body, needle):
    before = env.read_bytes()
    r = _client().put("/settings/llm", json=body)
    assert r.status_code == 422
    assert needle in r.json()["detail"]
    assert env.read_bytes() == before


def test_put_bad_json_and_big_body(env):
    c = _client()
    r = c.put("/settings/llm", content=b"{nope", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]
    r = c.put("/settings/llm", json={"model": "x", "pad": "y" * 5000})
    assert r.status_code == 413


@pytest.mark.parametrize("path", ["/settings/llm", "/settings/llm/models"])
def test_rejects_remote_and_foreign_origin(env, path):
    assert _client("192.168.1.20").get(path).status_code == 403
    assert _client().get(path, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _client().get(path, headers={"Origin": "chrome-extension://short"}).status_code == 403
    assert _client().get(path, headers={"Origin": "http://127.0.0.1:8766"}).status_code == 200
    r = _client("10.0.0.2").put("/settings/llm", json={"model": "x"})
    assert r.status_code == 403 and b"model=x" not in env.read_bytes()
    assert _client("10.0.0.2").post("/settings/llm/test", json={}).status_code == 403


def _status_error(status):
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": "nope"}})
    cls = {401: openai.AuthenticationError, 404: openai.NotFoundError}.get(status, openai.APIStatusError)
    return cls("nope", response=response, body=None)


@pytest.mark.parametrize(
    "exc, needle",
    [
        (_status_error(401), "Anahtar reddedildi (401)"),
        (_status_error(404), "Model bulunamadı (404)"),
        (_status_error(503), "(503)"),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), "Sunucuya ulaşılamadı"),
    ],
)
def test_test_endpoint_errors(env, monkeypatch, exc, needle):
    from backend import settings_api

    async def probe(base_url, api_key, model):
        raise exc

    monkeypatch.setattr(settings_api, "_chat_probe", probe)
    r = _client().post("/settings/llm/test", json={"api_key": SECRET})
    data = r.json()
    assert r.status_code == 200 and data["ok"] is False and needle in data["message"]
    assert SECRET not in r.text and isinstance(data["latency_ms"], int)


def test_test_endpoint_ok_uses_body_then_current(env, monkeypatch):
    from backend import settings_api

    seen = []

    async def probe(base_url, api_key, model):
        seen.append((base_url, api_key, model))

    monkeypatch.setattr(settings_api, "_chat_probe", probe)
    data = _client().post(
        "/settings/llm/test", json={"base_url": "https://evren-llmapi.ssyz.org.tr/v1", "model": "gemma-4-31b", "api_key": SECRET}
    ).json()
    assert data["ok"] is True and data["model"] == "gemma-4-31b"
    assert data["message"].startswith("Bağlantı başarılı · gemma-4-31b · ") and data["message"].endswith(" sn")
    assert seen[-1] == ("https://evren-llmapi.ssyz.org.tr/v1", SECRET, "gemma-4-31b")
    _client().post("/settings/llm/test", json={})
    assert seen[-1] == ("https://api.deepseek.com", "old-key-1234567890", "deepseek-chat")


def test_test_endpoint_without_key(env, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    from backend import config

    config.get_settings.cache_clear()
    data = _client().post("/settings/llm/test", json={}).json()
    assert data["ok"] is False and "anahtar" in data["message"].lower()


def test_chat_probe_honours_thinking_off(monkeypatch):
    """The real probe sends max_tokens and the enable_thinking=False extra_body."""
    import asyncio

    from backend import settings_api

    calls = []

    class FakeCompletions:
        async def create(self, **kw):
            calls.append(kw)

    class FakeSDK:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": FakeCompletions()})()

        async def close(self):
            pass

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeSDK)
    monkeypatch.setenv("LLM_THINKING", "off")
    asyncio.run(settings_api._chat_probe("https://x/v1", "k", "gemma-4-31b"))
    assert calls[0]["max_tokens"] == 20
    assert calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_models_api_and_preset_fallback(env, monkeypatch):
    from backend import settings_api

    async def ok(base_url, api_key):
        assert api_key == "old-key-1234567890"
        return ["m-1", "m-2"]

    monkeypatch.setattr(settings_api, "_fetch_remote_models", ok)
    data = _client().get("/settings/llm/models").json()
    assert data == {"models": [{"id": "m-1", "label": "m-1", "note": ""}, {"id": "m-2", "label": "m-2", "note": ""}], "source": "api"}

    async def fail(base_url, api_key):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(settings_api, "_fetch_remote_models", fail)
    data = _client().get("/settings/llm/models", params={"preset": "evren"}).json()
    assert data["source"] == "preset" and [m["id"] for m in data["models"]] == ["gemma-4-31b", "deepseek-v4.1-flash"]
    data = _client().get("/settings/llm/models", params={"base_url": "https://api.deepseek.com/"}).json()
    assert data["source"] == "preset" and data["models"][0]["id"] == "deepseek-chat"
    data = _client().get("/settings/llm/models", params={"base_url": "javascript:x"}).json()
    assert data["source"] == "preset"


def test_saved_key_is_never_sent_to_a_foreign_url():
    from backend.settings_api import _key_may_go_to

    saved = "https://evren-llmapi.ssyz.org.tr/v1"
    assert _key_may_go_to(saved + "/", saved)
    assert _key_may_go_to("https://api.deepseek.com", saved)
    assert not _key_may_go_to("https://attacker.example/v1", saved)
