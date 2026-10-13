"""Background long-term memory extraction (runs after every `final`).

One small JSON call (temperature 0) looks at the user's last message and pulls
out durable personal facts/preferences the user stated about *themselves*
("Benim adım Anıl", "kahveyi şekersiz içerim", "evim Kadıköy'de").  Each fact
goes through the secret filter and the similarity dedupe in
``backend.tools.memory.save_fact``; secrets (passwords, card numbers, API keys,
TC kimlik...) are never stored.  Failures are logged and swallowed.

Disable with ``MEMORY_EXTRACT=0``.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from backend import db
from backend.tools import memory

logger = logging.getLogger(__name__)

MAX_FACTS = 3
#: Messages shorter than this (or a single word) are never sent to the model.
MIN_CHARS = 8

SYSTEM_PROMPT = """# Hafıza çıkarıcı

Görevin: kullanıcının SON mesajında kendisi hakkında söylediği, ileride işe
yarayacak KALICI kişisel bilgileri ve tercihleri bulmak (adı, yaşadığı yer,
mesleği, aile bilgisi, sevdiği/sevmediği şeyler, alışkanlıkları, doğum günü).

Kurallar:
- Yalnızca kullanıcının kendisi hakkında açıkça söylediklerini al. Sorular,
  geçici istekler ("bugün hava nasıl", "şu sayfayı aç"), asistanın cevabındaki
  bilgiler ve genel bilgiler KALICI BİLGİ DEĞİLDİR.
- Şifre, PIN, kart numarası, IBAN, TC kimlik no, API anahtarı, token gibi gizli
  bilgileri ASLA çıkarma.
- Her bilgiyi üçüncü şahısla, kısa ve tek başına anlaşılır yaz; kullanıcının
  kullandığı anahtar kelimeyi koru. Ör. "Benim adım Anıl" -> "Kullanıcının adı
  (adım) Anıl."; "kahveyi şekersiz içerim" -> "Kullanıcı kahveyi şekersiz içer."
- En fazla 3 bilgi. Yoksa boş liste.

Yalnızca JSON döndür: {"facts": ["...", "..."]}
"""

_CUE = re.compile(
    r"\b(ben|benim|bana|beni|adım|ismim|hatırla|unutma|sev|tercih|evim|işim|yaşıyorum|"
    r"doğum|eşim|annem|babam|kızım|oğlum|çalışıyorum|alerji)",
)


def enabled() -> bool:
    db.load_env()
    return (os.environ.get("MEMORY_EXTRACT") or "1").strip().lower() not in {"0", "false", "no", "off"}


def worth_checking(user_text: str) -> bool:
    """Cheap gate so trivial messages ("selam", "teşekkürler") cost no LLM call."""

    text = memory.turkish_lower((user_text or "").strip())
    if len(text) < MIN_CHARS or len(text.split()) < 2:
        return False
    if memory.is_sensitive(text):
        return False
    # First-person cue or a first-person verb ending (içerim, yaşarım, severim...).
    return bool(_CUE.search(text) or re.search(r"\w{2,}(ım|im|um|üm)\b", text))


def _facts(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    raw = data.get("facts") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:MAX_FACTS]:
        if isinstance(item, dict):
            item = item.get("text") or item.get("fact") or ""
        text = " ".join(str(item or "").split())
        if 3 <= len(text) <= 300:
            out.append(text)
    return out


async def extract_and_store(client: Any, user_text: str, answer: str = "") -> list[int]:
    """Run the extraction call and store new facts; returns the stored ids."""

    if not enabled() or not worth_checking(user_text):
        return []
    if not bool(getattr(client, "configured", True)):
        return []
    prompt = f"Kullanıcının son mesajı:\n{user_text.strip()[:1000]}"
    try:
        data, _usage = await client.chat_json(
            SYSTEM_PROMPT, [{"role": "user", "content": prompt}], temperature=0.0
        )
    except Exception as exc:  # noqa: BLE001 - background work must never surface
        logger.info("Hafıza çıkarımı başarısız: %s", exc)
        return []
    stored: list[int] = []
    for fact in _facts(data):
        try:
            memory_id = await memory.save_fact(fact)
        except Exception:  # noqa: BLE001
            logger.exception("Hafıza kaydı yazılamadı")
            continue
        if memory_id is not None:
            logger.info("Kalıcı hafızaya eklendi (#%s)", memory_id)
            stored.append(memory_id)
    return stored
