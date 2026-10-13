"""Tool registry shared by the executor, the orchestrator and the agents.

Owner: K3.  Frozen contract: see ORTAK BAĞLAM section 7.  Every tool in the
system registers itself here; nothing is dispatched by name lookup elsewhere.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..bridge import BrowserBridge
    from ..trace import TraceStep

logger = logging.getLogger(__name__)

Risk = Literal["safe", "confirm"]
Route = Literal["chat", "web_search", "browser_task", "desktop_task", "personal"]

#: A click on an element whose label matches this pattern always needs the
#: user's explicit confirmation, even though the tool itself is "safe".
RISKY_PATTERN = re.compile(
    r"gönder|satın|öde|sipariş|sil|onayla|kaydet|iptal|abone|kaldır|transfer|havale|"
    r"submit|buy|pay|order|delete|send|confirm|purchase|checkout|subscribe|remove|cancel|transfer|withdraw|"
    r"hesab[ıi]\s*kapat|close\s*account|ödeme|paylaş|share|publish|yayınla",
    re.IGNORECASE,
)

#: Which tools the executor may see for each router decision.
ROUTE_TOOLS: dict[str, tuple[str, ...]] = {
    "chat": (),
    "web_search": ("web.search", "browser.read", "browser.navigate", "browser.open"),
    "browser_task": (
        "browser.snapshot",
        "browser.click",
        "browser.type",
        "browser.scroll",
        "browser.navigate",
        "browser.open",
        "browser.read",
        "browser.highlight",
        "web.search",
    ),
    "desktop_task": (
        "desktop.snapshot",
        "desktop.click",
        "desktop.type",
        "desktop.key",
        "desktop.scroll",
    ),
    "personal": ("memory.save", "memory.search", "reminders.add", "reminders.list"),
}

ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable[dict[str, Any]]]


@dataclass
class Tool:
    """A single callable capability exposed to the LLM."""

    name: str  # dotted, canonical: "browser.click"
    description: str  # Turkish, shown to the LLM
    parameters: dict[str, Any]  # JSON Schema, object root
    risk: Risk
    handler: ToolHandler

    def __post_init__(self) -> None:
        if "." not in self.name:
            raise ValueError(f"Tool names are namespaced, got {self.name!r}")
        if self.parameters.get("type") != "object":
            raise ValueError(f"Tool {self.name} parameters must be an object schema")


@dataclass
class ToolContext:
    """Everything a handler is allowed to touch."""

    request_id: str
    bridge: "BrowserBridge"
    tab: dict[str, Any] | None
    emit_trace: Callable[["TraceStep"], Awaitable[None]]
    extras: dict[str, Any] = field(default_factory=dict)


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """Register (or replace) a tool.  Duplicates are logged loudly."""

    if tool.name in _REGISTRY:
        logger.warning("Tool %s re-registered, previous handler replaced", tool.name)
    _REGISTRY[tool.name] = tool


def get(name: str) -> Tool | None:
    """Canonical lookup; accepts both `browser.click` and `browser__click`."""

    return _REGISTRY.get(name) or _REGISTRY.get(from_llm_name(name))


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def list_for(route: str) -> list[Tool]:
    """Tools visible to the executor for a router route."""

    names = ROUTE_TOOLS.get(route)
    if names is None:
        logger.warning("Unknown route %r, no tools exposed", route)
        return []
    tools = [t for t in (_REGISTRY.get(n) for n in names) if t is not None]
    fallback = (os.environ.get("WEB_SEARCH_FALLBACK") or "duckduckgo").strip().lower()
    if not (os.environ.get("BRAVE_API_KEY") or "").strip() and fallback in ("off", "none", "0", "false"):
        # No key and no keyless fallback: web.search can only fail.
        tools = [t for t in tools if t.name != "web.search"]
    missing = [n for n in names if n not in _REGISTRY]
    if missing:
        logger.info("Route %s still misses tools: %s", route, ", ".join(missing))
    return tools


def clear() -> None:
    """Testing helper: forget every registration."""

    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# LLM name mangling: OpenAI-compatible function names reject dots.
# ---------------------------------------------------------------------------

_LLM_SEP = "__"


def llm_name(name: str) -> str:
    return name.replace(".", _LLM_SEP)


def from_llm_name(name: str) -> str:
    head, _, tail = name.partition(_LLM_SEP)
    return f"{head}.{tail}" if tail else name


def to_llm_schema(tool: Tool) -> dict[str, Any]:
    """OpenAI `tools=[...]` entry for one tool."""

    return {
        "type": "function",
        "function": {
            "name": llm_name(tool.name),
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def to_llm_schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    return [to_llm_schema(t) for t in tools]


def inject_why(parameters: dict[str, Any]) -> dict[str, Any]:
    """Copy of a schema with the executor-mandated `why` argument.

    The executor strips `why` before calling the handler; it only feeds the
    explanation timeline.  Never mutates the registered schema.
    """

    import copy

    schema = copy.deepcopy(parameters)
    props = schema.setdefault("properties", {})
    props["why"] = {
        "type": "string",
        "description": "Bu adımı neden attığını tek cümleyle açıkla.",
    }
    required = list(schema.get("required", []))
    if "why" not in required:
        required.append("why")
    schema["required"] = required
    return schema


def to_llm_schema_with_why(tool: Tool) -> dict[str, Any]:
    schema = to_llm_schema(tool)
    schema["function"]["parameters"] = inject_why(tool.parameters)
    return schema


def is_risky_label(text: str | None) -> bool:
    """Deterministic risk gate: does this element label look destructive?"""

    if not text:
        return False
    return bool(RISKY_PATTERN.search(text))


#: Text that means "yes" when the model sends `submit` as a string/number.
_TRUTHY = frozenset({"1", "true", "yes", "on", "evet", "tamam"})


def is_truthy(value: Any) -> bool:
    """Strict-ish truthiness for model-provided flags such as `submit`.

    The confirmation gate must fire whenever the handler would actually submit,
    and the handler receives whatever the model sent.  `submit=1` or
    `submit="true"` therefore counts as submitting, while `"false"`/`0`/`None`
    do not.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def find_element(snapshot: dict[str, Any] | None, ref: Any) -> dict[str, Any] | None:
    """Resolve an element ref against the latest snapshot."""

    if not isinstance(snapshot, dict):
        return None
    try:
        wanted = int(ref)
    except (TypeError, ValueError):
        return None
    elements = snapshot.get("elements")
    if not isinstance(elements, list):
        return None
    for element in elements:
        if isinstance(element, dict) and element.get("ref") == wanted:
            return element
    return None


_SEARCH_FIELD = re.compile(r"\b(ara|arama|search|sorgu|bul|query|find)\b|^q$", re.IGNORECASE)


def _is_search_field(element: dict[str, Any]) -> bool:
    """A search box: role searchbox, or a textbox/combobox labelled like a search."""

    role = str(element.get("role") or "").lower()
    if role == "searchbox":
        return True
    if role not in {"textbox", "combobox", "input", ""}:
        return False
    label = " ".join(str(element.get(k) or "") for k in ("name", "placeholder")).strip()
    return bool(label) and bool(_SEARCH_FIELD.search(label)) and not is_risky_label(label)


def needs_confirm(tool: Tool, args: dict[str, Any], snapshot: dict[str, Any] | None) -> str | None:
    """Return a Turkish confirmation summary when the gate must fire.

    `None` means "just run it".  Rules (ORTAK BAĞLAM section 7):
      * tool.risk == "confirm"                        -> always ask
      * browser.click / desktop.click                 -> ask when the target label
                                                         matches the risky pattern
      * browser.type with submit=true                 -> ask before submitting
    The gate is deterministic and runs *before* every tool call.
    """

    if tool.risk == "confirm":
        return f"`{tool.name}` aracı onay gerektiriyor. Çalıştırılsın mı?"

    if tool.name == "browser.type" and is_truthy(args.get("submit")):
        # Submitting a search box is harmless; everything else (forms, comments,
        # messages, checkout fields) needs the user's OK.
        target = find_element(snapshot, args.get("ref"))
        if target is not None and _is_search_field(target):
            return None
        return "Formdaki metin gönderilsin mi?"

    if tool.name not in {"browser.click", "desktop.click"}:
        return None

    element = find_element(snapshot, args.get("ref"))
    if element is None:
        # Unknown ref: the click cannot be proven harmless, so ask.  A ref that
        # does not exist in the latest snapshot is also a model mistake the
        # executor should fix — see the "snapshot'ı yenile" prompt rule.
        return "Hedef öğe son snapshot'ta bulunamadı. Yine de tıklansın mı?"

    label = element.get("name") or element.get("value")
    if is_risky_label(label):
        shown = (label or "").strip()[:80]
        return f"“{shown}” öğesine tıklanmasını onaylıyor musun?"
    return None


#: Labels/roles of fields the assistant must never type into (PROTOCOL section 1).
_SECRET_FIELD = re.compile(r"password|passwd|parola|şifre|sifre|cvv|cvc|kart\s*no|card\s*number", re.IGNORECASE)


def blocked_reason(tool: Tool, args: dict[str, Any], snapshot: dict[str, Any] | None) -> str | None:
    """Hard refusal (no confirmation possible): typing into password/card fields."""

    if tool.name != "browser.type":
        return None
    element = find_element(snapshot, args.get("ref"))
    if element is None:
        return None
    haystack = " ".join(str(element.get(k) or "") for k in ("role", "name", "type", "autocomplete"))
    if _SECRET_FIELD.search(haystack):
        return "Şifre/kart alanlarına yazmak güvenlik nedeniyle engellendi."
    return None


def click_needs_confirm(tool: Tool, args: dict[str, Any], snapshot: dict[str, Any] | None) -> str | None:
    """Backwards-compatible alias for :func:`needs_confirm`."""

    return needs_confirm(tool, args, snapshot)


# ---------------------------------------------------------------------------
# Sensitive-field detection (trace masking) and argument validation.
# ---------------------------------------------------------------------------

#: Broader than `_SECRET_FIELD`: used only to *mask* typed text in traces, so a
#: false positive just hides a harmless value.
_SENSITIVE_FIELD = re.compile(
    r"password|passwd|passcode|parola|şifre|sifre|\bpin\b|cvv|cvc|kart|card|credit|\bcc\b|cc-|iban|\botp\b",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    # Python's casefold maps "İ" to "i̇" (two code points); fold Turkish dotted I first.
    return text.replace("İ", "i").replace("I", "i").lower()


def is_sensitive_element(element: dict[str, Any] | None) -> bool:
    """Does a snapshot element look like a password / card / CVV field?"""

    if not isinstance(element, dict):
        return False
    haystack = " ".join(
        str(element.get(key) or "") for key in ("role", "name", "type", "autocomplete", "placeholder")
    )
    return bool(_SENSITIVE_FIELD.search(_fold(haystack)))


_JSON_TYPE_TR = {
    "string": "metin",
    "integer": "tam sayı",
    "number": "sayı",
    "boolean": "true/false",
    "array": "liste",
    "object": "nesne",
}
_BOOL_STRINGS = frozenset({"true", "false", "1", "0", "yes", "no", "on", "off", "evet", "hayır", "hayir", "tamam"})
_INT_RE = re.compile(r"^\s*-?\d+\s*$")
_NUM_RE = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")


def _type_ok(expected: str, value: Any) -> bool:
    """Basic JSON-schema type check, lenient where LLMs commonly stringify."""

    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return value.is_integer()
        return isinstance(value, str) and bool(_INT_RE.match(value))
    if expected == "number":
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float)) or (isinstance(value, str) and bool(_NUM_RE.match(value)))
    if expected == "boolean":
        if isinstance(value, bool):
            return True
        if isinstance(value, int):
            return value in (0, 1)
        return isinstance(value, str) and value.strip().lower() in _BOOL_STRINGS
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True  # unknown type keyword: do not block


def validate_args(tool: Tool, args: Any) -> str | None:
    """Validate model-provided arguments against the tool's JSON schema.

    Checks required fields, basic types, `enum` and array item types.  Extra
    properties (including the executor's `why`) are ignored.  Returns a Turkish
    error the executor can act on, or `None` when the arguments are usable.
    """

    if not isinstance(args, dict):
        return f"`{tool.name}` argümanları bir JSON nesnesi olmalı."
    schema = tool.parameters or {}
    props: dict[str, Any] = schema.get("properties") or {}
    problems: list[str] = []

    for name in schema.get("required") or []:
        if name == "why":
            continue
        if name not in args or args[name] is None or (isinstance(args[name], str) and not args[name].strip()):
            problems.append(f"`{name}` zorunlu ama eksik")

    for name, spec in props.items():
        if name == "why" or name not in args or not isinstance(spec, dict):
            continue
        value = args[name]
        if value is None and name not in (schema.get("required") or []):
            continue
        expected = spec.get("type")
        types = expected if isinstance(expected, list) else [expected] if expected else []
        if types and not any(_type_ok(t, value) for t in types):
            wanted = " veya ".join(_JSON_TYPE_TR.get(t, str(t)) for t in types)
            problems.append(f"`{name}` {wanted} olmalı (gelen: {type(value).__name__})")
            continue
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            allowed = ", ".join(repr(v) for v in enum)
            problems.append(f"`{name}` şunlardan biri olmalı: {allowed}")
            continue
        items = spec.get("items")
        if isinstance(value, list) and isinstance(items, dict) and items.get("type"):
            if not all(_type_ok(items["type"], v) for v in value):
                problems.append(f"`{name}` öğeleri {_JSON_TYPE_TR.get(items['type'], items['type'])} olmalı")

    if not problems:
        return None
    return f"Geçersiz argümanlar ({tool.name}): " + "; ".join(problems) + ". Argümanları düzeltip tekrar dene."
