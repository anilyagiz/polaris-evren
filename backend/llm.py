"""DeepSeek client (OpenAI-compatible).  Owner: K3.

Two entry points only: strict JSON completion for the reasoning agents and
function calling for the executor.  Both report latency and token usage so the
timeline can show them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from openai import APIError, AsyncOpenAI

try:  # openai<2 exported AsyncTimeout; openai>=2 renamed it to APITimeoutError
    from openai import AsyncTimeout  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - depends on the installed SDK major
    from openai import APITimeoutError as AsyncTimeout

from .config import get_settings

logger = logging.getLogger(__name__)

Message = dict[str, Any]


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or returns unusable output."""


@dataclass
class Usage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1
    finish_reason: str | None = None

    @property
    def tokens(self) -> int:
        return self.total_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
        }


@dataclass
class ToolCall:
    """Normalised tool call, decoupled from the SDK object."""

    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None


@dataclass
class ToolMessage:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


_RETRY_HINT = "Sadece geçerli JSON döndür. Açıklama, markdown veya kod bloğu ekleme."


class LLMClient:
    """Thin async wrapper; one instance per process."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = base_url or settings.deepseek_base_url
        self.model = model or settings.llm_model
        self.timeout_s = timeout_s if timeout_s is not None else settings.llm_timeout_s
        self._client: AsyncOpenAI | None = None

    # -- plumbing ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _sdk(self) -> AsyncOpenAI:
        if not self.api_key:
            raise LLMError("DEEPSEEK_API_KEY tanımlı değil; backend/.env dosyasını doldur.")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=1,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            finally:
                self._client = None

    @staticmethod
    def _usage(response: Any, model: str, latency_ms: int, attempts: int) -> Usage:
        raw = getattr(response, "usage", None)
        finish = None
        try:
            finish = response.choices[0].finish_reason
        except Exception:  # pragma: no cover
            pass
        return Usage(
            model=getattr(response, "model", model) or model,
            prompt_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(raw, "total_tokens", 0) or 0),
            latency_ms=latency_ms,
            attempts=attempts,
            finish_reason=finish,
        )

    # -- entry points -----------------------------------------------------
    async def chat_json(
        self,
        system: str,
        messages: Sequence[Message],
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], Usage]:
        """Strict JSON answer.  Retries once with a blunt reminder."""

        payload: list[Message] = [{"role": "system", "content": system}, *messages]
        attempts = 0
        last_error: Exception | None = None

        for attempt in range(2):
            attempts += 1
            started = time.perf_counter()
            try:
                response = await self._sdk().chat.completions.create(
                    model=self.model,
                    messages=payload,  # type: ignore[arg-type]
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
            except (APIError, AsyncTimeout, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning("LLM json call failed (attempt %s): %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                raise LLMError(f"LLM çağrısı başarısız: {exc}") from exc

            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = self._usage(response, self.model, latency_ms, attempts)
            content = (response.choices[0].message.content or "").strip()
            try:
                return _loads_lenient(content), usage
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning("Model returned invalid JSON (attempt %s): %.200s", attempt + 1, content)
                if attempt == 0:
                    payload = [
                        *payload,
                        {"role": "assistant", "content": content[:2000]},
                        {"role": "user", "content": _RETRY_HINT},
                    ]
                    continue
        raise LLMError(f"Model geçerli JSON döndürmedi: {last_error}")

    async def chat_tools(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        temperature: float = 0.2,
    ) -> tuple[ToolMessage, Usage]:
        """Function-calling turn; returns normalised tool calls."""

        payload: list[Message] = [{"role": "system", "content": system}, *messages]
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = list(tools)
            kwargs["tool_choice"] = "auto"
        try:
            response = await self._sdk().chat.completions.create(**kwargs)
        except (APIError, AsyncTimeout, asyncio.TimeoutError) as exc:
            logger.warning("LLM tool call failed: %s", exc)
            raise LLMError(f"LLM çağrısı başarısız: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = self._usage(response, self.model, latency_ms, 1)
        message = response.choices[0].message
        calls: list[ToolCall] = []
        for raw in message.tool_calls or []:
            name = getattr(getattr(raw, "function", None), "name", None) or "unknown"
            raw_args = getattr(getattr(raw, "function", None), "arguments", None) or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                if not isinstance(args, dict):
                    raise ValueError("arguments is not an object")
                calls.append(ToolCall(id=getattr(raw, "id", "") or "call_0", name=name, args=args))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                calls.append(
                    ToolCall(
                        id=getattr(raw, "id", "") or "call_0",
                        name=name,
                        args={},
                        parse_error=f"arguments JSON okunamadı: {exc}",
                    )
                )
        return ToolMessage(content=(message.content or "").strip(), tool_calls=calls), usage


def _loads_lenient(content: str) -> dict[str, Any]:
    """Parse JSON, tolerating ```json fences the model sometimes adds."""

    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise json.JSONDecodeError("top-level JSON must be an object", text, 0)
    return data


_CLIENT: LLMClient | None = None


def get_llm() -> LLMClient:
    """Process-wide singleton so connection pools are shared."""

    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LLMClient()
    return _CLIENT


def set_llm(client: LLMClient | None) -> None:
    """Test/CLI hook."""

    global _CLIENT
    _CLIENT = client
