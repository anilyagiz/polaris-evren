"""DeepSeek client (OpenAI-compatible).  Owner: K3.

Two entry points only: strict JSON completion for the reasoning agents and
function calling for the executor.  Both report latency and token usage so the
timeline can show them.
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI

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


_MAX_CALL_ATTEMPTS = 3
_BACKOFF_S = (0.6, 1.5)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (AsyncTimeout, asyncio.TimeoutError, APIConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(exc, APIStatusError) or isinstance(status, int):
        return status == 429 or (isinstance(status, int) and status >= 500)
    return False


def _describe_error(exc: Exception | None) -> str:
    """Turkish, user-presentable explanation of a failed LLM call."""

    if exc is None:
        return "LLM çağrısı başarısız."
    status = getattr(exc, "status_code", None)
    if isinstance(exc, (AsyncTimeout, asyncio.TimeoutError)):
        return "LLM zaman aşımına uğradı; biraz sonra tekrar dene."
    if isinstance(exc, APIConnectionError):
        return "LLM sunucusuna bağlanılamadı; DEEPSEEK_BASE_URL ve internet bağlantını kontrol et."
    if status in (401, 403):
        return "LLM kimlik doğrulaması reddedildi; DEEPSEEK_API_KEY değerini kontrol et."
    if status == 404:
        return "LLM adresi veya modeli bulunamadı; DEEPSEEK_BASE_URL ve LLM_MODEL değerlerini kontrol et."
    if status == 429:
        return "LLM istek sınırına takıldı (429); biraz bekleyip tekrar dene."
    if isinstance(status, int) and status >= 500:
        return f"LLM sunucusu geçici hata verdi ({status}); biraz sonra tekrar dene."
    return f"LLM çağrısı başarısız: {exc}"


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
        return self.config_problem() is None

    def config_problem(self) -> str | None:
        """Turkish description of what is missing, or None when usable."""

        if not (self.api_key or "").strip():
            return "DEEPSEEK_API_KEY tanımlı değil; backend/.env dosyasını doldur."
        base = (self.base_url or "").strip()
        if not base.startswith(("http://", "https://")):
            return "DEEPSEEK_BASE_URL geçersiz veya boş (http:// ya da https:// ile başlamalı); backend/.env dosyasını kontrol et."
        if not (self.model or "").strip():
            return "LLM_MODEL tanımlı değil; backend/.env dosyasında modeli (örn. deepseek-chat) belirt."
        return None

    def _sdk(self) -> AsyncOpenAI:
        problem = self.config_problem()
        if problem:
            raise LLMError(problem)
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=0,  # retries/backoff are handled in _create
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

    # -- resilient call ---------------------------------------------------
    async def _create(self, **kwargs: Any) -> Any:
        """One completion call with small backoff on 429/5xx/timeouts/connection errors."""

        last: Exception | None = None
        for attempt in range(_MAX_CALL_ATTEMPTS):
            try:
                return await self._sdk().chat.completions.create(**_with_thinking(kwargs, self.model))
            except LLMError:
                raise
            except (APIError, AsyncTimeout, asyncio.TimeoutError) as exc:
                last = exc
                if not _is_transient(exc) or attempt == _MAX_CALL_ATTEMPTS - 1:
                    break
                delay = _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
                logger.warning("LLM geçici hata (deneme %s), %.1f sn sonra tekrar: %s", attempt + 1, delay, exc)
                await asyncio.sleep(delay)
        fallback = (os.environ.get("LLM_FALLBACK_MODEL") or "").strip()
        status = getattr(last, "status_code", None)
        if fallback and kwargs.get("model") != fallback and status not in (401, 403):
            logger.warning(
                "LLM modeli %s başarısız (%s); yedek modele geçiliyor: %s",
                kwargs.get("model"), _describe_error(last), fallback,
            )
            return await self._create(**{**kwargs, "model": fallback})
        raise LLMError(_describe_error(last)) from last

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
            response = await self._create(
                model=self.model,
                messages=payload,
                temperature=temperature,
                response_format={"type": "json_object"},
            )

            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = self._usage(response, self.model, latency_ms, attempts)
            try:
                content = (response.choices[0].message.content or "").strip()
            except (IndexError, AttributeError, TypeError) as exc:
                raise LLMError(f"Model boş/bozuk yanıt döndürdü: {exc}") from exc
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
        response = await self._create(**kwargs)

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = self._usage(response, self.model, latency_ms, 1)
        try:
            message = response.choices[0].message
        except (IndexError, AttributeError, TypeError) as exc:
            raise LLMError(f"Model boş/bozuk yanıt döndürdü: {exc}") from exc
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
        content = (message.content or "").strip()
        if not calls and tools and content:
            # Some models (e.g. Gemma behind the EVREN gateway) write the call as
            # JSON text instead of using native tool_calls; recover it.
            calls = _text_tool_calls(content, tools)
            if calls:
                logger.info("Metin içindeki araç çağrısı ayrıştırıldı: %s", [c.name for c in calls])
                content = ""
        return ToolMessage(content=content, tool_calls=calls), usage


def _with_thinking(kwargs: dict[str, Any], primary_model: str) -> dict[str, Any]:
    """LLM_THINKING=off disables hidden reasoning on the primary model (Gemma: ~5x faster)."""

    if (os.environ.get("LLM_THINKING") or "").strip().lower() not in ("off", "0", "false", "no"):
        return kwargs
    if kwargs.get("model") != primary_model:
        return kwargs
    extra = dict(kwargs.get("extra_body") or {})
    extra.setdefault("chat_template_kwargs", {"enable_thinking": False})
    return {**kwargs, "extra_body": extra}


def _text_tool_calls(content: str, tools: Sequence[dict[str, Any]]) -> list[ToolCall]:
    """Parse ``{"name": ..., "arguments": {...}}`` (or a list of them) written as text."""

    names: set[str] = set()
    for tool in tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        if fn.get("name"):
            names.add(str(fn["name"]))
    if not names:
        return []
    import re

    candidates: list[Any] = []
    text = content.strip()
    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S) or [text]:
        try:
            candidates.append(json.loads(block))
            continue
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            candidates.append(_loads_lenient(block))
        except (json.JSONDecodeError, ValueError):
            continue
    calls: list[ToolCall] = []
    for item in candidates:
        for obj in item if isinstance(item, list) else [item]:
            if not isinstance(obj, dict):
                continue
            if isinstance(obj.get("function"), dict):
                obj = obj["function"]
            name = obj.get("name") or obj.get("tool")
            if not isinstance(name, str):
                continue
            name = name.strip()
            if name not in names and name.replace(".", "__") in names:
                name = name.replace(".", "__")
            if name not in names:
                continue
            args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append(ToolCall(id=f"text_{len(calls)}", name=name, args=args))
    return calls


def _loads_lenient(content: str) -> dict[str, Any]:
    """Parse JSON, tolerating code fences and prose around the object."""

    import re

    text = content.strip()
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    elif text.startswith("```"):
        text = text.split("\n", 1)[-1].strip()
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
        _CLIENT._from_settings = True  # type: ignore[attr-defined]
    return _CLIENT


def reload_llm() -> None:
    """Point the shared client at the current settings (after /settings/llm PUT).

    Reconfigured in place so open conversations (orchestrators and agents keep
    a reference to the same instance) pick up the new key/URL/model on their
    next call.  Clients injected via :func:`set_llm` (tests, CLI stubs) are
    left alone.
    """

    client = _CLIENT
    if client is None or not getattr(client, "_from_settings", False):
        return
    settings = get_settings()
    client.api_key = settings.deepseek_api_key
    client.base_url = settings.deepseek_base_url
    client.model = settings.llm_model
    client.timeout_s = settings.llm_timeout_s
    # The old SDK object keeps serving any in-flight call; new calls build a fresh one.
    client._client = None


def set_llm(client: LLMClient | None) -> None:
    """Test/CLI hook."""

    global _CLIENT
    _CLIENT = client
