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
                    json.dumps(tool.get("args") or {}, ensure_ascii=False) if tool else None,
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


def make_emitter(send: SendFn, store: TraceStore, request_id: str) -> EmitFn:
    """Bind a TraceStep sink to one request: send frame -> then persist."""

    async def emit(step: TraceStep) -> TraceStep:
        try:
            await send(step.to_event(request_id))
        except Exception as exc:
            logger.warning("Trace emit failed for %s: %s", request_id, exc)
        await store.save(request_id, step)
        return step

    return emit
