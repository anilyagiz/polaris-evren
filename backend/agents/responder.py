"""Responder agent: turns the evidence collected so far into the final answer.

Owner: K3.  Frozen signature: see INTERFACES.md section 8.

The response is evidence-bound: the prompt forbids inventing facts that are not
in the collected evidence, and the citations are derived from that evidence
(never trusted blindly from the model).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..trace import EmitFn
from .base import (
    BaseAgent,
    Citation,
    FinalAnswer,
    HistoryEntry,
    render_snapshot,
)

logger = logging.getLogger(__name__)

TEMPERATURE = 0.4
MAX_CITATIONS = 5
MAX_EVIDENCE_IN_PROMPT = 20
MAX_HISTORY_LINES = 12

#: Fallback texts: the user must always get an answer, even when the LLM dies.
FALLBACK_ANSWER = "Üzgünüm, cevap üretilemedi. (LLM hatası)"
FALLBACK_SPOKEN = "Üzgünüm, şu anda cevap veremiyorum."


class ResponderAgent(BaseAgent):
    """Writes the user-facing answer (markdown) plus its spoken form."""

    name = "responder"
    prompt_file = "responder.md"

    async def respond(
        self,
        *,
        goal: str,
        route: str,
        result_summary: str,
        evidence: list[str],
        snapshot: dict[str, Any] | None,
        history: list[HistoryEntry],
        emit: EmitFn,
    ) -> FinalAnswer:
        evidence = list(evidence or [])
        url_evidence = [item for item in evidence if item.startswith("url:")]
        extra_citations = self._citations_from_history(history)

        # Section names mirror the input format documented in responder.md.
        lines = [
            "## VERİ",
            f"Hedef: {goal or '(belirtilmedi)'}",
            f"Rota: {route or 'chat'}",
            f"Sonuç özeti: {result_summary or '(özet yok)'}",
        ]
        if url_evidence:
            lines.append("Kanıt bağlantıları:")
            lines.extend(f"- {item}" for item in url_evidence[:MAX_EVIDENCE_IN_PROMPT])
        lines.append("Kanıtlar:")
        if evidence:
            lines.extend(f"- {item}" for item in evidence[:MAX_EVIDENCE_IN_PROMPT])
        else:
            lines.append("- (kanıt yok)")
        lines.append("## Son snapshot")
        lines.append(render_snapshot(snapshot))
        lines.append("## Araç geçmişi")
        if history:
            lines.extend(entry.render() for entry in history[-MAX_HISTORY_LINES:])
        else:
            lines.append("- (araç çağrısı yok)")
        lines.append(
            "Cevabı yalnızca bu kanıtlara dayandır; kanıtta olmayan bilgiyi uydurma. "
            "Kanıt zayıfsa bunu açıkça belirt."
        )

        try:
            data, usage = await self.complete_json(
                [{"role": "user", "content": "\n".join(lines)}],
                temperature=TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 - the user must always get an answer
            # LLMError, or a malformed-but-"valid" provider response
            # (IndexError/KeyError/AttributeError inside llm.py): answer with the
            # Turkish fallback *and* a trace step instead of an `error` frame.
            logger.warning("Responder LLM hatası (%s): %s", type(exc).__name__, exc)
            await self.emit_step(
                emit,
                decision="Cevap hazırlanamadı",
                why="LLM hatası, yedek cevap kullanıldı",
                evidence=evidence[:MAX_CITATIONS],
            )
            return FinalAnswer(answer=FALLBACK_ANSWER, spoken=FALLBACK_SPOKEN, citations=[])

        if not isinstance(data, dict):  # defensive: complete_json promises a dict
            data = {}
        answer_text = str(data.get("answer", "") or "").strip()
        spoken = str(data.get("spoken", "") or "").strip()
        citations = self._merge_citations(
            self._citations_from_model(data.get("citations")),
            extra_citations,
            self._citations_from_evidence(evidence),
        )
        # Pass primitives: FinalAnswer._clean only keeps dict/str citation items.
        drafted = FinalAnswer(
            answer=answer_text or spoken or FALLBACK_ANSWER,
            spoken=spoken,
            citations=[citation.model_dump() for citation in citations],
        )
        # INTERFACES.md §9: `spoken` is plain text, at most two sentences, no
        # markdown/URLs.  spoken_text() enforces that here and falls back to the
        # first sentences of `answer` when the model left `spoken` empty.
        drafted.spoken = drafted.spoken_text() or FALLBACK_SPOKEN

        await self.emit_step(
            emit,
            decision="Cevap hazırlandı",
            why=f"{len(citations)} kanıt kullanıldı",
            evidence=evidence[:MAX_CITATIONS],
            usage=usage,
        )
        return drafted

    # -- citation helpers -------------------------------------------------
    @staticmethod
    def _citations_from_evidence(evidence: Sequence[str]) -> list[Citation]:
        """`url:...` prefixed evidence lines become citations."""

        out: list[Citation] = []
        for item in evidence:
            if not item.startswith("url:"):
                continue
            url = item[len("url:") :].strip()
            if url:
                out.append(Citation(title=_title_from_url(url), url=url))
        return out

    @staticmethod
    def _citations_from_history(history: Sequence[HistoryEntry]) -> list[Citation]:
        """Collect `web.search` results of successful calls, whatever the shape.

        K4 owns the `web.search` payload, so this must not depend on one exact
        envelope: we walk inside a list of results as well as a plain list of
        dicts (with or without a `data`/`results` wrapper) and only accept dicts.
        """

        out: list[Citation] = []
        for entry in history:
            if entry.tool != "web.search" or not entry.ok:
                continue
            for item in _iter_results(entry.data):
                if not isinstance(item, dict):
                    continue
                url = str(
                    item.get("url") or item.get("link") or item.get("href") or ""
                ).strip()
                if not url:
                    continue
                title = str(item.get("title") or item.get("name") or "").strip()
                out.append(
                    Citation(
                        title=title or _title_from_url(url),
                        url=url,
                        ref=_as_ref(item.get("ref")),
                    )
                )
        return out

    @staticmethod
    def _citations_from_model(raw: Any) -> list[Citation]:
        """Accept both `{title, url, ref}` objects and bare URL strings."""

        if not isinstance(raw, list):
            return []
        out: list[Citation] = []
        for item in raw:
            if isinstance(item, str):
                url = item.strip()
                if url:
                    out.append(Citation(title=_title_from_url(url), url=url))
            elif isinstance(item, dict):
                url = str(item.get("url", "") or "").strip()
                if not url:
                    continue
                title = str(item.get("title", "") or "").strip() or _title_from_url(url)
                out.append(Citation(title=title, url=url, ref=_as_ref(item.get("ref"))))
        return out

    @staticmethod
    def _merge_citations(*groups: Sequence[Citation]) -> list[Citation]:
        """Deduplicate by normalised URL, keep the first title, cap at 5."""

        merged: list[Citation] = []
        seen: set[str] = set()
        for group in groups:
            for citation in group:
                key = _normalise_url(citation.url)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(
                    Citation(
                        title=citation.title or _title_from_url(citation.url),
                        url=citation.url,
                        ref=citation.ref,
                    )
                )
                if len(merged) >= MAX_CITATIONS:
                    return merged
        return merged


def _as_ref(raw: Any) -> int | None:
    """`citations[].ref` is the evidence `ref:` number, or null (responder.md)."""

    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _iter_results(data: Any) -> list[Any]:
    """Flat list of candidate result entries, tolerating every plausible shape.

    `[ ... ]`, `{"results": [...]}`, `{"data": {"results": [...]}}` and
    `{"data": [ ... ]}` are all accepted.
    """

    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("results", "data", "items", "hits"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _iter_results(value)
            if nested:
                return nested
    return []


def _normalise_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _title_from_url(url: str) -> str:
    """Human-ish title fallback: the hostname."""

    text = url.strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.split("/", 1)[0] or url
