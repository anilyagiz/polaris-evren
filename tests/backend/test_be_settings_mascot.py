"""PUT/GET /settings/mascot write the shared tray-settings.json switch."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client():
    from backend.main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_get_put_roundtrip_preserves_other_keys(tmp_path, monkeypatch):
    path = tmp_path / "tray-settings.json"
    path.write_text(json.dumps({"window": {"x": 1}, "mascot": {"enabled": False, "right": 10}}), encoding="utf-8")
    monkeypatch.setenv("POLARIS_TRAY_SETTINGS", str(path))
    c = _client()
    assert c.get("/settings/mascot").json()["enabled"] is False
    assert c.put("/settings/mascot", json={"enabled": True}).json()["enabled"] is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["window"] == {"x": 1} and data["mascot"] == {"enabled": True, "right": 10}


def test_put_validates_and_guards(tmp_path, monkeypatch):
    monkeypatch.setenv("POLARIS_TRAY_SETTINGS", str(tmp_path / "t.json"))
    c = _client()
    assert c.put("/settings/mascot", json={"enabled": "yes"}).status_code == 422
    assert c.put("/settings/mascot", json={"enabled": True}, headers={"Origin": "https://evil.example"}).status_code == 403
