"""Fake OpenAI-compatible LLM for the REAL-browser E2E (scripts/browser_e2e/run.mjs).

Reuses scripts/e2e/fake_llm.py unchanged and only swaps two scripted scenarios
so they work against a real DOM snapshot of extension/test/fixture.html:

* ``shop_search`` ("... mekanik klavye ara, ilk 3 fiyatı söyle")
  snapshot -> type "Ürün ara" -> click "Ara" -> read -> highlight first
  "Sepete ekle" -> finish with the first three prices found in the HISTORY.
* ``form`` ("Bu formu doldur ve gönder")
  snapshot -> type "Ad soyad" -> type "E-posta" -> click "Gönder" (risky label,
  so the backend's gate must send need_confirm) -> finish.

Refs are looked up by accessible name in the SNAPSHOT block the executor is
shown, never hard-coded: if the extension's snapshot does not reach the
backend, the script cannot continue (just like a real model).

Run:  .venv\\Scripts\\python scripts\\browser_e2e\\fake_llm_browser.py
Prints one JSON line {"base_url": ...} on stdout, then serves until killed.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.e2e import fake_llm  # noqa: E402

_original_executor_action = fake_llm.executor_action
_original_responder_reply = fake_llm._responder_reply

PRICE_RE = re.compile(r"\d{1,3}(?:\.\d{3})*(?:,\d+)?\s?TL")


def _ref(refs: dict[str, int], *names: str) -> int | None:
    for name in names:
        if name in refs:
            return refs[name]
    return None


def _all_refs(user: str, name: str) -> list[int]:
    block = fake_llm._section(user, "## SNAPSHOT\n", "\n\n## Görev")
    return [int(m.group(1)) for m in re.finditer(r'^\[(\d+)\] \S+ "(.*?)"', block, re.M) if m.group(2) == name]


def executor_action(scenario: str, text: str, user: str) -> tuple:
    hist = fake_llm.parse_history(user)
    names = [h.tool for h in hist]
    refs = fake_llm.snapshot_refs(user)

    if any(h.status == "rejected" for h in hist):
        return ("finish", "Kullanıcı gönderimi reddetti, form gönderilmedi.")

    if scenario == "shop_search":
        if "browser.snapshot" not in names:
            return ("tool", "browser.snapshot", {})
        search = _ref(refs, "Ürün ara", "Ara")
        if search is None:
            return ("finish", "Snapshot'ta arama kutusu bulunamadı.")
        if "browser.type" not in names:
            return ("tool", "browser.type", {"ref": search, "text": "mekanik klavye"})
        button = _ref(refs, "Ara")
        if "browser.click" not in names and button is not None:
            return ("tool", "browser.click", {"ref": button})
        if "browser.read" not in names:
            return ("tool", "browser.read", {})
        carts = _all_refs(user, "Sepete ekle")
        if "browser.highlight" not in names and carts:
            return ("tool", "browser.highlight", {"ref": carts[0]})
        body = " ".join(h.body for h in hist if h.tool == "browser.read")
        prices = PRICE_RE.findall(body)[:3]
        if len(prices) < 3:
            return ("finish", f"Fiyatlar okunamadı (bulunan: {prices}).")
        return ("finish", f"İlk 3 fiyat: {', '.join(prices)}.")

    if scenario == "form":
        if "browser.snapshot" not in names:
            return ("tool", "browser.snapshot", {})
        name_ref = _ref(refs, "Ad soyad")
        mail_ref = _ref(refs, "E-posta")
        send_ref = _ref(refs, "Gönder")
        if None in (name_ref, mail_ref, send_ref):
            return ("finish", f"Form alanları snapshot'ta bulunamadı: {sorted(refs)[:20]}")
        typed = [h for h in hist if h.tool == "browser.type"]
        if len(typed) == 0:
            return ("tool", "browser.type", {"ref": name_ref, "text": "Ayşe Yılmaz"})
        if len(typed) == 1:
            return ("tool", "browser.type", {"ref": mail_ref, "text": "ayse@example.com"})
        if "browser.click" not in names:
            return ("tool", "browser.click", {"ref": send_ref})
        if fake_llm._called(hist, "browser.click", "error"):
            return ("finish", "Gönder düğmesine basılamadı.")
        return ("finish", "Form dolduruldu ve gönderildi.")

    return _original_executor_action(scenario, text, user)


def responder_reply(scenario: str, user: str) -> dict[str, Any]:
    reply = _original_responder_reply(scenario, user)
    if scenario == "shop_search":
        # A page-element citation so the panel's source chip (#ref) is exercised.
        m = re.search(r"browser\.highlight\(\{[^)]*?['\"]ref['\"]: (\d+)", user)
        page = re.search(r"url: (https?://\S+)", user)
        if m and page:
            # The backend drops citations without a url, so cite the page itself.
            reply["citations"] = [{"title": "İlk ürün", "url": page.group(1), "ref": int(m.group(1))}]
    return reply


fake_llm.executor_action = executor_action
fake_llm._responder_reply = responder_reply


def main() -> None:
    srv = fake_llm.FakeLLMServer()
    srv.start()
    print(json.dumps({"base_url": srv.base_url}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
