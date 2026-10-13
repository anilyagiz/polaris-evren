"""A12 guvenlik testleri: kodu degistirmeden dogrulanabilen davranislar.

Bilinen aciklar xfail(strict=False) ile isaretli; duzeltilince XPASS olur.
"""
from __future__ import annotations

import pytest

from backend.tools import registry

shots = pytest.importorskip("backend.cua.shots_router")


@pytest.mark.parametrize(
    "name",
    ["../secret.png", "..%2Fsecret.png", "a/b.png", "a\b.png", "x.png/../../y.png",
     "shot.png.exe", "", ".png", "shot.PNG\n", ".."],
)
def test_shots_name_rejected(name):
    assert not shots._VALID_NAME.fullmatch(name)


def test_shots_name_accepted():
    assert shots._VALID_NAME.fullmatch("desktop_2024-01-01_ab12.png")


@pytest.mark.parametrize(
    "label",
    ["GÖNDER", "SİL", "Sil", "SATIN AL", "ÖDE", "Ödeme yap", "ONAYLA", "Sipariş Ver",
     "DELETE", "Submit", "Pay now", "Send", "Confirm"],
)
def test_risky_labels_detected(label):
    assert registry.is_risky_label(label)


@pytest.mark.parametrize("label", ["Ara", "Geri", "Menü", "", None])
def test_safe_labels_not_flagged(label):
    assert not registry.is_risky_label(label)


@pytest.mark.xfail(strict=False, reason="A12 bulgu: desen kapsamı dar (bkz. docs/_reports/A12.md)")
@pytest.mark.parametrize("label", ["Purchase", "Checkout", "Kaydet", "İptal et", "Abone ol",
                                   "Remove", "Transfer", "Kaldır", "Hesabı kapat"])
def test_risky_pattern_coverage_gaps(label):
    assert registry.is_risky_label(label)


@pytest.mark.parametrize("value,expected", [(True, True), ("true", True), ("1", True), (1, True),
                                            ("evet", True), (False, False), ("false", False),
                                            (0, False), (None, False), ([], False)])
def test_is_truthy(value, expected):
    assert registry.is_truthy(value) is expected


def _tool(name):
    return registry.Tool(name=name, description="", parameters={"type": "object"}, risk="safe", handler=None) \
        if hasattr(registry, "Tool") else None


def test_type_submit_requires_confirm():
    tool = _tool("browser.type")
    assert registry.needs_confirm(tool, {"ref": 1, "text": "x", "submit": "true"}, {"elements": []})
    assert registry.needs_confirm(tool, {"ref": 1, "text": "x"}, {"elements": []}) is None


def test_click_unknown_ref_and_risky_label_confirm():
    tool = _tool("browser.click")
    snap = {"elements": [{"ref": 1, "name": "GÖNDER"}, {"ref": 2, "name": "Ara"}]}
    assert registry.needs_confirm(tool, {"ref": 1}, snap)
    assert registry.needs_confirm(tool, {"ref": 2}, snap) is None
    assert registry.needs_confirm(tool, {"ref": 99}, snap)  # bilinmeyen ref -> sor


@pytest.mark.xfail(strict=False, reason="A12 bulgu: HOST varsayilani 0.0.0.0")
def test_default_host_is_loopback(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    from backend import config
    s = config.get_settings() if not hasattr(config.get_settings, "cache_clear") else (
        config.get_settings.cache_clear() or config.get_settings())
    assert s.host in ("127.0.0.1", "localhost", "::1")
