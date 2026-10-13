"""Mock-based tests for /stt and /tts (no models, no network)."""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.voice import stt as stt_mod
from backend.voice import tts as tts_mod

app = FastAPI()
app.include_router(stt_mod.router)
app.include_router(tts_mod.router)
client = TestClient(app)

AUDIO = b"\x1aE\xdf\xa3" + b"\x00" * 4000


class FakeModel:
    def transcribe(self, path, **kw):
        seg = types.SimpleNamespace(text=" Merhaba dünya ", end=1.5)
        return iter([seg]), types.SimpleNamespace(duration=1.5, language="tr")


@pytest.fixture
def fake_libs(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    monkeypatch.setitem(sys.modules, "av", types.ModuleType("av"))
    monkeypatch.setattr(stt_mod, "_get_model", lambda: FakeModel())


def post_audio(data=AUDIO, name="a.webm", ct="audio/webm"):
    return client.post("/stt", files={"audio": (name, data, ct)})


@pytest.mark.parametrize("name,ct", [("a.webm", "audio/webm"), ("a.ogg", "audio/ogg"),
                                      ("a.wav", "audio/wav"), ("a.m4a", "audio/mp4"),
                                      ("blob", "audio/webm;codecs=opus")])
def test_stt_ok(fake_libs, name, ct):
    r = post_audio(name=name, ct=ct)
    assert r.status_code == 200
    assert r.json() == {"text": "Merhaba dünya", "language": "tr", "duration_s": 1.5}


def test_stt_empty_and_short(fake_libs):
    assert post_audio(b"").json()["text"] == ""
    assert post_audio(b"abc").json()["text"] == ""


def test_stt_too_large(fake_libs, monkeypatch):
    monkeypatch.setattr(stt_mod, "MAX_BYTES", 2000)
    r = post_audio(AUDIO)
    assert r.status_code == 413 and "büyük" in r.json()["detail"]


def test_stt_missing_lib_503(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # forces ImportError
    r = post_audio()
    assert r.status_code == 503 and "faster-whisper" in r.json()["detail"]


def test_stt_failure_500(fake_libs, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(stt_mod, "_get_model", boom)
    assert post_audio().status_code == 500


def test_model_loaded_once_under_concurrency(monkeypatch):
    calls = []
    fake = types.ModuleType("faster_whisper")

    class WM:
        def __init__(self, *a, **k):
            calls.append(1)
            time.sleep(0.2)
    fake.WhisperModel = WM
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(stt_mod, "_MODEL", None)
    ts = [threading.Thread(target=stt_mod._get_model) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1
    monkeypatch.setattr(stt_mod, "_MODEL", None)


# ---------------------------------------------------------------- TTS
def test_clean_text():
    out = tts_mod.clean_text("**Selam** [site](https://x.com) https://a.b 🎧\n- madde")
    assert "http" not in out and "*" not in out and "🎧" not in out
    assert out.startswith("Selam site")
    assert len(tts_mod.clean_text("a " * 1000)) <= tts_mod.MAX_CHARS + 1


@pytest.fixture
def tts_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_mod, "TTS_CACHE_DIR", tmp_path)
    calls = []

    async def attempt(text, voice, target):
        calls.append(text)
        target.write_bytes(b"ID3" + b"\x01" * 2000)
    monkeypatch.setattr(tts_mod, "_edge_attempt", attempt)
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    return calls, tmp_path


def test_tts_ok_and_cache(tts_env):
    calls, d = tts_env
    r = client.post("/tts", json={"text": "**Merhaba** dünya"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert "max-age" in r.headers["cache-control"]
    assert r.content.startswith(b"ID3")
    r2 = client.post("/tts", json={"text": "**Merhaba** dünya"})
    assert r2.content == r.content and len(calls) == 1
    assert len(list(d.glob("*.mp3"))) == 1 and not list(d.glob("*.tmp"))


def test_tts_blank_400(tts_env):
    assert client.post("/tts", json={"text": " *** "}).status_code == 400


def test_tts_bad_body_422(tts_env):
    assert client.post("/tts", json={}).status_code == 422


def test_tts_failure_503(tts_env, monkeypatch):
    async def fail(*a):
        raise OSError("offline")
    monkeypatch.setattr(tts_mod, "_edge_attempt", fail)
    monkeypatch.setattr(tts_mod, "_RETRY_SLEEP_S", 0)
    r = client.post("/tts", json={"text": "merhaba"})
    assert r.status_code == 503 and "tarayıcı" in r.json()["detail"]


def test_tts_timeout_503(tts_env, monkeypatch):
    import asyncio

    async def slow(*a):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(tts_mod, "_edge_attempt", slow)
    r = client.post("/tts", json={"text": "merhaba"})
    assert r.status_code == 503 and "zaman" in r.json()["detail"]


def test_tts_missing_lib_503(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_mod, "TTS_CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    assert client.post("/tts", json={"text": "merhaba"}).status_code == 503
