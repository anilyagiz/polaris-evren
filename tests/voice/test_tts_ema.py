"""EMA Lightning engine for /tts — model mocked (no download, no torch work)."""
from __future__ import annotations

import io
import sys
import types
import wave

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.voice import tts as tts_mod

app = FastAPI()
app.include_router(tts_mod.router)
client = TestClient(app)

TEXT = "Merhaba, size nasıl yardımcı olabilirim?"


class FakeEMA:
    def __init__(self):
        self.calls = []

    def say(self, text, speed=1.0, seed=None, sample_rate=48000, path=None):
        self.calls.append({"text": text, "speed": speed, "seed": seed, "sample_rate": sample_rate})
        n = int(0.07 * len(text) * sample_rate)
        audio = (0.3 * np.sin(np.arange(n) * 2 * np.pi * 220 / sample_rate)).astype(np.float32)
        return types.SimpleNamespace(audio=audio, sample_rate=sample_rate, duration=n / sample_rate)


@pytest.fixture
def ema_env(monkeypatch, tmp_path):
    """Default engine (ema) with a fake model; edge-tts recorded but never networked."""
    monkeypatch.delenv("TTS_ENGINE", raising=False)
    monkeypatch.delenv("TTS_SPEED", raising=False)
    monkeypatch.setattr(tts_mod, "TTS_CACHE_DIR", tmp_path)
    monkeypatch.setattr(tts_mod, "_EMA_MODEL", None)
    monkeypatch.setattr(tts_mod, "_EMA_DISABLED", False)
    monkeypatch.setattr(tts_mod, "_EMA_WARNED", False)
    monkeypatch.setattr(tts_mod, "_MP3_OK", None)
    monkeypatch.setattr(tts_mod, "ema_importable", lambda: True)
    fake = FakeEMA()
    monkeypatch.setattr(tts_mod, "_get_ema", lambda: fake)

    edge_calls = []

    async def attempt(text, voice, target):
        edge_calls.append(text)
        target.write_bytes(b"ID3" + b"\x02" * 500)
    monkeypatch.setattr(tts_mod, "_edge_attempt", attempt)
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    return fake, edge_calls, tmp_path


def test_ema_is_default_and_returns_decodable_mp3(ema_env):
    fake, edge_calls, d = ema_env
    r = client.post("/tts", json={"text": TEXT})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert not edge_calls and len(fake.calls) == 1
    call = fake.calls[0]
    assert call["sample_rate"] == 24000 and call["seed"] == tts_mod.EMA_SEED and call["speed"] == 1.0

    import av
    with av.open(io.BytesIO(r.content)) as f:
        stream = f.streams.audio[0]
        samples = sum(fr.samples for fr in f.decode(stream))
        assert stream.rate == 24000
    seconds = samples / 24000
    assert 0.06 * len(TEXT) <= seconds <= 0.1 * len(TEXT)


def test_ema_cache_hit_skips_model(ema_env):
    fake, _, d = ema_env
    r1 = client.post("/tts", json={"text": TEXT})
    r2 = client.post("/tts", json={"text": TEXT})
    assert r1.content == r2.content and len(fake.calls) == 1
    assert len(list(d.glob("*.mp3"))) == 1 and not list(d.glob("*.tmp"))


def test_speed_env_is_passed_and_clamped(ema_env, monkeypatch):
    fake, _, _ = ema_env
    monkeypatch.setenv("TTS_SPEED", "1.25")
    client.post("/tts", json={"text": TEXT})
    monkeypatch.setenv("TTS_SPEED", "9")
    client.post("/tts", json={"text": TEXT})
    monkeypatch.setenv("TTS_SPEED", "abc")
    client.post("/tts", json={"text": TEXT})
    assert [c["speed"] for c in fake.calls] == [1.25, 4.0, 1.0]


def test_cache_key_includes_engine_speed_format(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_mod, "TTS_CACHE_DIR", tmp_path)
    base = tts_mod._cache_path("selam", "v", "ema", 1.0, "mp3")
    assert base.suffix == ".mp3"
    others = {
        tts_mod._cache_path("selam", "v", "edge", 1.0, "mp3"),
        tts_mod._cache_path("selam", "v", "ema", 1.5, "mp3"),
        tts_mod._cache_path("selam", "v", "ema", 1.0, "wav"),
        tts_mod._cache_path("selam", "w", "ema", 1.0, "mp3"),
        tts_mod._cache_path("selam!", "v", "ema", 1.0, "mp3"),
    }
    assert base not in others and len(others) == 5
    assert tts_mod._cache_path("selam", "v", "ema", 1.0, "mp3") == base


def test_wav_when_no_mp3_encoder(ema_env, monkeypatch):
    monkeypatch.setattr(tts_mod, "_MP3_OK", False)
    r = client.post("/tts", json={"text": TEXT})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
    assert list(ema_env[2].glob("*.wav"))


def test_wav_when_mp3_encoding_fails(ema_env, monkeypatch):
    def boom(*a):
        raise RuntimeError("no lame")
    monkeypatch.setattr(tts_mod, "_encode_mp3", boom)
    r = client.post("/tts", json={"text": TEXT})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content.startswith(b"RIFF")


def test_fallback_to_edge_when_ema_missing(ema_env, monkeypatch, caplog):
    fake, edge_calls, _ = ema_env
    monkeypatch.setattr(tts_mod, "ema_importable", lambda: False)
    with caplog.at_level("WARNING", logger=tts_mod.__name__):
        r1 = client.post("/tts", json={"text": "bir"})
        r2 = client.post("/tts", json={"text": "iki"})
    assert r1.status_code == r2.status_code == 200
    assert r1.headers["content-type"] == "audio/mpeg" and r1.content.startswith(b"ID3")
    assert edge_calls == ["bir", "iki"] and not fake.calls
    assert sum("not available" in m for m in caplog.messages) == 1  # logged once


def test_fallback_to_edge_when_ema_load_fails(ema_env, monkeypatch):
    _, edge_calls, _ = ema_env

    def broken():
        raise ImportError("No module named 'torch'")
    monkeypatch.setattr(tts_mod, "_get_ema", broken)
    r = client.post("/tts", json={"text": TEXT})
    assert r.status_code == 200 and r.content.startswith(b"ID3") and edge_calls == [TEXT]


def test_import_failure_disables_ema(monkeypatch):
    monkeypatch.setattr(tts_mod, "_EMA_MODEL", None)
    monkeypatch.setattr(tts_mod, "_EMA_DISABLED", False)
    monkeypatch.setitem(sys.modules, "ema_lightning", None)  # import -> ImportError
    with pytest.raises(ImportError):
        tts_mod._get_ema()
    assert tts_mod._EMA_DISABLED is True
    with pytest.raises(RuntimeError):
        tts_mod._get_ema()


def test_edge_engine_skips_ema(ema_env, monkeypatch):
    fake, edge_calls, _ = ema_env
    monkeypatch.setenv("TTS_ENGINE", "edge")
    r = client.post("/tts", json={"text": TEXT})
    assert r.status_code == 200 and edge_calls == [TEXT] and not fake.calls


def test_engine_env_parsing(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "EDGE")
    assert tts_mod.tts_engine() == "edge"
    monkeypatch.setenv("TTS_ENGINE", "piper")
    assert tts_mod.tts_engine() == "ema"
    monkeypatch.delenv("TTS_ENGINE")
    assert tts_mod.tts_engine() == "ema"


def test_tts_available_either_engine(monkeypatch):
    monkeypatch.setattr(tts_mod, "_EMA_DISABLED", False)
    monkeypatch.setattr(tts_mod, "ema_importable", lambda: True)
    assert tts_mod.tts_available() is True
    monkeypatch.setattr(tts_mod, "ema_importable", lambda: False)
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    monkeypatch.setattr(tts_mod.importlib.util, "find_spec", lambda name: None)
    assert tts_mod.tts_available() is False
