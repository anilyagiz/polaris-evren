"""Explainability trace: schema, WebSocket emitter and SQLite persistence.

Owner: K3.  The TraceStep shape is frozen by ORTAK BAĞLAM section 6.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import ensure_data_dir, now_iso

logger = logging.getLogger(__name__)

AgentName = Literal["router", "planner", "executor", "judge", "responder", "system"]
Verdict = Literal["pass", "revise", "ask_user"]

SendFn = Callable[[dict[str, Any]], Awaitable[None]]
EmitFn = Callable[["TraceStep"], Awaitable["TraceStep"]]
SnapshotFn = Callable[[], "dict[str, Any] | None"]

#: Shown instead of text typed into password / card fields.
MASK = "••••"
#: Longest string value kept in `traces.tool_args` (SQLite).
MAX_STORED_ARG_CHARS = 200
#: Upper bound for `TraceStore.history(limit=...)` (and GET /traces).
MAX_HISTORY_LIMIT = 500

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    decision TEXT NOT NULL,
    why TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    tool_name TEXT,
    tool_args TEXT,
    scores TEXT,
    verdict TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER,
    ts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_traces_request ON traces(request_id)"


class ToolInvocation(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class JudgeScores(BaseModel):
    correct: int = Field(ge=1, le=5)
    complete: int = Field(ge=1, le=5)
    safe: int = Field(ge=1, le=5)


class TraceStep(BaseModel):
    """One explained decision of one agent."""

    agent: AgentName
    decision: str
    why: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tool: ToolInvocation | None = None
    scores: JudgeScores | None = None
    verdict: Verdict | None = None
    latency_ms: int = 0
    tokens: int | None = None
    ts: str = Field(default_factory=now_iso)

    def to_event(self, request_id: str) -> dict[str, Any]:
        return {"type": "trace", "request_id": request_id, "step": self.to_wire()}

    def to_wire(self) -> dict[str, Any]:
        """JSON-ready dict; `null` fields stay present but explicit."""

        return self.model_dump(mode="json")


def make_step(
    agent: AgentName,
    decision: str,
    why: str,
    *,
    evidence: list[str] | None = None,
    confidence: float | None = None,
    tool: ToolInvocation | dict[str, Any] | None = None,
    scores: JudgeScores | dict[str, Any] | None = None,
    verdict: Verdict | None = None,
    latency_ms: int = 0,
    tokens: int | None = None,
) -> TraceStep:
    """Convenience constructor used by agents and the orchestrator."""

    if isinstance(tool, dict):
        tool = ToolInvocation(**tool)
    if isinstance(scores, dict):
        try:
            scores = JudgeScores(**scores)
        except Exception:  # pragma: no cover - model returned junk
            logger.warning("Discarding invalid judge scores: %s", scores)
            scores = None
    return TraceStep(
        agent=agent,
        decision=decision,
        why=why,
        evidence=list(evidence or []),
        confidence=confidence,
        tool=tool,
        scores=scores,
        verdict=verdict,
        latency_ms=int(latency_ms or 0),
        tokens=tokens,
    )


# ---------------------------------------------------------------------------
# Redaction: secrets never leave the process in a trace (sent or stored).
# ---------------------------------------------------------------------------
def redact_tool_args(
    name: str | None, args: dict[str, Any] | None, snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    """Copy of `args` with text typed into sensitive fields replaced by MASK.

    `browser.type` into an element whose role/name/type/autocomplete looks like
    a password, PIN, card number or CVV is masked.  When no snapshot is known
    the field cannot be classified, so only the name check applies.
    """

    out = dict(args or {})
    if name != "browser.type" or "text" not in out:
        return out
    from .tools.registry import find_element, is_sensitive_element

    if is_sensitive_element(find_element(snapshot, out.get("ref"))):
        out["text"] = MASK
    return out


def cap_strings(value: Any, limit: int = MAX_STORED_ARG_CHARS) -> Any:
    """Recursively truncate long strings (keeps the DB small and leaks short)."""

    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: cap_strings(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [cap_strings(v, limit) for v in value]
    return value


def redact_step(step: TraceStep, snapshot: dict[str, Any] | None) -> TraceStep:
    """Return `step` (or a masked copy) safe to send to the panel and persist."""

    if step.tool is None:
        return step
    masked = redact_tool_args(step.tool.name, step.tool.args, snapshot)
    if masked == step.tool.args:
        return step
    return step.model_copy(update={"tool": ToolInvocation(name=step.tool.name, args=masked)})


class TraceStore:
    """Writes every step to SQLite; emitting to the panel is the caller's job."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        self._ready = False

    @property
    def db_path(self) -> str:
        return self._db_path or str(ensure_data_dir())

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)
        self._ready = True

    def _init_sync(self) -> None:
        path = self.db_path
        with sqlite3.connect(path, timeout=10) as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_INDEX)
            conn.commit()

    async def save(self, request_id: str, step: TraceStep) -> None:
        if not self._ready:
            await self.init()
        payload = step.to_wire()
        async with self._lock:
            try:
                await asyncio.to_thread(self._save_sync, request_id, payload)
            except Exception as exc:  # tracing must never break a request
                logger.error("Could not persist trace step: %s", exc)

    def _save_sync(self, request_id: str, step: dict[str, Any]) -> None:
        tool = step.get("tool") or {}
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO traces (request_id, agent, decision, why, evidence, confidence,
                                    tool_name, tool_args, scores, verdict, latency_ms, tokens, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    step["agent"],
                    step["decision"],
                    step["why"],
                    json.dumps(step.get("evidence") or [], ensure_ascii=False),
                    step.get("confidence"),
                    tool.get("name"),
                    json.dumps(cap_strings(tool.get("args") or {}), ensure_ascii=False) if tool else None,
                    json.dumps(step.get("scores"), ensure_ascii=False) if step.get("scores") else None,
                    step.get("verdict"),
                    int(step.get("latency_ms") or 0),
                    step.get("tokens"),
                    step["ts"],
                ),
            )
            conn.commit()

    async def history(self, request_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Read back a request's timeline (used by /trace and debugging)."""

        limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
        return await asyncio.to_thread(self._history_sync, request_id, limit)

    def _history_sync(self, request_id: str, limit: int) -> list[dict[str, Any]]:
        if not self._ready:
            self._init_sync()
            self._ready = True
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM traces WHERE request_id = ? ORDER BY id ASC LIMIT ?",
                (request_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


def make_emitter(
    send: SendFn,
    store: TraceStore,
    request_id: str,
    snapshot_provider: SnapshotFn | None = None,
) -> EmitFn:
    """Bind a TraceStep sink to one request: redact -> send frame -> persist.

    `snapshot_provider` returns the latest page snapshot so text typed into
    password/card fields can be masked; the caller's step is never mutated.
    """

    async def emit(step: TraceStep) -> TraceStep:
        snapshot = None
        if snapshot_provider is not None:
            try:
                snapshot = snapshot_provider()
            except Exception:  # pragma: no cover - defensive
                snapshot = None
        safe = redact_step(step, snapshot)
        try:
            await send(safe.to_event(request_id))
        except Exception as exc:
            logger.warning("Trace emit failed for %s: %s", request_id, exc)
        await store.save(request_id, safe)
        return step

    return emit
