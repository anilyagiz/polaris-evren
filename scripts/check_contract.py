"""Statik sözleşme denetleyicisi (LLM'siz, ağsız, DB'siz). Owner: K3.

Kullanım (cwd = assistant/):
    python scripts/check_contract.py

Exit 0: hiç blocker yok.  Exit 1: en az bir blocker var.
Çıktı Türkçedir; her denetim için PASS/WARN/FAIL satırı basılır.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "shared" / "fixtures" / "session_example.jsonl"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BROWSER_TOOL_COUNT = 8
BROWSER_TOOLS = frozenset(
    {
        "browser.snapshot",
        "browser.click",
        "browser.type",
        "browser.scroll",
        "browser.navigate",
        "browser.open",
        "browser.read",
        "browser.highlight",
    }
)

FROZEN_ROUTES = frozenset({"chat", "web_search", "browser_task", "desktop_task", "personal"})

#: Tools whose modules belong to K4/K5 and may legitimately be absent for now.
EXPECTED_LATER = frozenset(
    {
        "web.search",
        "desktop.snapshot",
        "desktop.click",
        "desktop.type",
        "desktop.key",
        "desktop.scroll",
        "memory.save",
        "memory.search",
        "reminders.add",
        "reminders.list",
    }
)

ALLOWED_FRAME_TYPES = frozenset(
    {
        "hello",
        "user_msg",
        "new_session",
        "session",
        "tool_result",
        "confirm",
        "cancel",
        "trace",
        "tool_call",
        "need_confirm",
        "final",
        "notify",
        "error",
    }
)

TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")

#: module, class, method, keyword-only parameter names required by INTERFACES.md §8.
AGENT_CONTRACTS: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("backend.agents.router", "RouterAgent", "route", frozenset({"user_text", "tab", "memories", "emit"})),
    ("backend.agents.planner", "PlannerAgent", "plan", frozenset({"user_text", "route", "tab", "memories", "emit"})),
    (
        "backend.agents.executor",
        "ExecutorAgent",
        "step",
        frozenset({"goal", "plan", "route", "history", "snapshot", "tools", "emit"}),
    ),
    ("backend.agents.judge", "JudgeAgent", "judge", frozenset({"goal", "plan", "history", "snapshot", "emit"})),
    (
        "backend.agents.responder",
        "ResponderAgent",
        "respond",
        frozenset({"goal", "route", "result_summary", "evidence", "snapshot", "history", "emit"}),
    ),
)


@dataclass
class Result:
    """One check outcome: PASS / WARN / FAIL plus Turkish bullet lines."""

    status: str
    lines: list[str] = field(default_factory=list)


class Reporter:
    """Collects results, prints them and counts blockers/warnings."""

    def __init__(self) -> None:
        self.blockers = 0
        self.warnings = 0
        self.failed: list[str] = []
        self._index = 0

    def run(self, title: str, check: Callable[[], Result]) -> None:
        self._index += 1
        try:
            result = check()
        except Exception as exc:  # a check must never take the report down
            logger.exception("Denetim çöktü: %s", title)
            result = Result("FAIL", [f"beklenmeyen hata: {exc.__class__.__name__}: {exc}"])
        if result.status == "FAIL":
            self.blockers += 1
            self.failed.append(title)
        elif result.status == "WARN":
            self.warnings += 1
        print(f"[{self._index}] {title}  ->  {result.status}")
        for line in result.lines:
            print(f"      - {line}")
        print()


def _summary(problems: list[str], warnings: list[str], notes: list[str]) -> Result:
    """Build a result from three buckets: FAIL / WARN / bilgi."""

    if problems:
        return Result("FAIL", [*notes, *problems, *warnings])
    if warnings:
        return Result("WARN", [*notes, *warnings])
    return Result("PASS", notes)


# ---------------------------------------------------------------------------
# 1. browser.* kaydı
# ---------------------------------------------------------------------------
def check_browser_registration() -> Result:
    try:
        importlib.import_module("backend.tools.browser")
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.browser içe aktarılamadı: {exc!r}"])

    names = {tool.name for tool in registry.all_tools()}
    found = sorted(name for name in names if name.startswith("browser."))
    missing = sorted(BROWSER_TOOLS - names)
    unexpected = sorted(set(found) - BROWSER_TOOLS)

    notes = [f"kayıtlı browser.* aracı: {len(found)}/{BROWSER_TOOL_COUNT} -> {', '.join(found) or '(yok)'}"]

    if missing:
        return Result("FAIL", [*notes, f"eksik browser tool: {', '.join(missing)}"])
    if unexpected:
        notes.append(f"fazladan browser.* kaydı (sözleşmede yok): {', '.join(unexpected)}")
    return Result("PASS", notes)


# ---------------------------------------------------------------------------
# 2. Kayıtlı tool kayıtları
# ---------------------------------------------------------------------------
def check_tool_records() -> Result:
    try:
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.registry içe aktarılamadı: {exc!r}"])

    tools = registry.all_tools()
    if not tools:
        return Result("FAIL", ["registry boş: hiç tool kayıtlı değil"])

    problems: list[str] = []
    for tool in tools:
        if "." not in tool.name:
            problems.append(f"{tool.name}: ad namespace'li değil ('.' yok)")

        params = tool.parameters
        if not isinstance(params, dict) or params.get("type") != "object":
            problems.append(f"{tool.name}: parameters type 'object' değil")
            continue

        props = params.get("properties")
        required = params.get("required", [])
        if not isinstance(props, dict):
            problems.append(f"{tool.name}: properties sözlük değil")
            props = {}
        if not isinstance(required, list):
            problems.append(f"{tool.name}: required liste değil")
        else:
            unknown = [str(item) for item in required if item not in props]
            if unknown:
                problems.append(f"{tool.name}: required içinde tanımsız argüman: {', '.join(unknown)}")

        if tool.risk not in ("safe", "confirm"):
            problems.append(f"{tool.name}: risk {tool.risk!r} geçersiz ('safe'|'confirm' bekleniyor)")

        description = (tool.description or "").strip()
        if not description:
            problems.append(f"{tool.name}: description boş")
        elif not TURKISH_CHARS.search(description):
            problems.append(f"{tool.name}: description Türkçe karakter içermiyor (Türkçe metin bekleniyor)")

    notes = [
        f"denetlenen tool: {len(tools)}",
        "her tool için: namespace, object şeması, required tutarlılığı, risk ve Türkçe açıklama kontrol edildi",
    ]
    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# 3. llm_name / from_llm_name round-trip
# ---------------------------------------------------------------------------
def check_name_roundtrip() -> Result:
    try:
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.registry içe aktarılamadı: {exc!r}"])

    tools = registry.all_tools()
    problems: list[str] = []
    for tool in tools:
        mangled = registry.llm_name(tool.name)
        restored = registry.from_llm_name(mangled)
        if "." in mangled:
            problems.append(f"{tool.name}: llm adı hâlâ '.' içeriyor ({mangled})")
        if restored != tool.name:
            problems.append(f"{tool.name}: round-trip bozuk -> {mangled} -> {restored}")

    notes = [f"round-trip denenen tool: {len(tools)} (örnek: browser.click <-> {registry.llm_name('browser.click')})"]
    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# 4. inject_why kopya güvenliği + 'why' zorunluluğu
# ---------------------------------------------------------------------------
def check_inject_why() -> Result:
    try:
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.registry içe aktarılamadı: {exc!r}"])

    tools = registry.all_tools()
    if not tools:
        return Result("FAIL", ["registry boş: inject_why denetlenecek tool yok"])

    problems: list[str] = []
    for tool in tools:
        original = copy.deepcopy(tool.parameters)
        schema = registry.inject_why(tool.parameters)

        if tool.parameters != original:
            problems.append(f"{tool.name}: inject_why kayıtlı şemayı DEĞİŞTİRDİ")
        if schema is tool.parameters:
            problems.append(f"{tool.name}: inject_why aynı sözlüğü döndürdü (deepcopy yok)")

        props = schema.get("properties") if isinstance(schema, dict) else None
        required = schema.get("required") if isinstance(schema, dict) else None
        if not isinstance(props, dict) or "why" not in props:
            problems.append(f"{tool.name}: 'why' properties içine eklenmedi")
        if not isinstance(required, list) or "why" not in required:
            problems.append(f"{tool.name}: 'why' required listesine eklenmedi")

        # to_llm_schema_with_why de kayıtlı şemayı korumalı.
        llm_schema = registry.to_llm_schema_with_why(tool)
        llm_params = llm_schema.get("function", {}).get("parameters", {})
        if "why" not in (llm_params.get("required") or []):
            problems.append(f"{tool.name}: to_llm_schema_with_why required'ında 'why' yok")
        if tool.parameters != original:
            problems.append(f"{tool.name}: to_llm_schema_with_why kayıtlı şemayı DEĞİŞTİRDİ")

    notes = [f"inject_why denenen tool: {len(tools)}", "'why' → properties + required, kaynak sözlük değişmiyor"]
    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# 5. ROUTE_TOOLS tutarlılığı
# ---------------------------------------------------------------------------
def check_route_tools() -> Result:
    try:
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.registry içe aktarılamadı: {exc!r}"])

    for _mod in ("browser", "desktop", "search", "memory", "reminders"):
        try:
            importlib.import_module(f"backend.tools.{_mod}")
        except Exception:  # missing K4/K5 module -> reported as warning below
            pass
    registered = {tool.name for tool in registry.all_tools()}
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    for route, names in registry.ROUTE_TOOLS.items():
        if route not in FROZEN_ROUTES:
            problems.append(f"bilinmeyen route: {route!r} (dondurulmuş kümede yok)")

        missing = [name for name in names if name not in registered]
        for name in missing:
            if name in EXPECTED_LATER:
                warnings.append(f"{route} -> {name}: kayıtlı değil, bekleniyor (K4/K5)")
            else:
                warnings.append(f"{route} -> {name}: kayıtlı değil ve beklenen modül listesinde de yok")

        try:
            visible = {tool.name for tool in registry.list_for(route)}
        except Exception as exc:
            problems.append(f"list_for({route!r}) hata verdi: {exc!r}")
            continue
        expected_visible = set(names) - set(missing)
        if visible != expected_visible:
            problems.append(
                f"list_for({route!r}) beklenen küme farklı: {sorted(visible)} != {sorted(expected_visible)}"
            )

    notes.append(
        f"route sayısı: {len(registry.ROUTE_TOOLS)}; kayıtlı tool: {len(registered)}; "
        f"eksik (uyarı) referans: {len(warnings)}"
    )
    return _summary(problems, warnings, notes)


# ---------------------------------------------------------------------------
# 6. Onay kapısı determinizmi
# ---------------------------------------------------------------------------
async def _noop_handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"ok": True}


def check_confirm_gate() -> Result:
    try:
        from backend.tools import registry
    except Exception as exc:
        return Result("FAIL", [f"backend.tools.registry içe aktarılamadı: {exc!r}"])

    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    confirm_tool = registry.Tool(
        name="check.confirm",
        description="Denetim için üretilmiş sahte tool.",
        parameters={"type": "object", "properties": {}},
        risk="confirm",
        handler=_noop_handler,
    )
    if registry.needs_confirm(confirm_tool, {}, None) is None:
        problems.append("risk='confirm' tool için onay özeti boş döndü")
    else:
        notes.append("risk='confirm' tool -> onay istiyor")

    type_tool = registry.get("browser.type")
    if type_tool is not None:
        if registry.needs_confirm(type_tool, {"ref": 3, "text": "x", "submit": True}, None) is None:
            warnings.append("browser.type submit=true için onay istenmiyor (beklenen: onay)")
        else:
            notes.append("browser.type submit=true -> onay istiyor")

    click = registry.get("browser.click")
    if click is None:
        problems.append("browser.click kayıtlı değil; tıklama kapısı test edilemedi")
    else:
        risky_snapshot = {"elements": [{"ref": 5, "role": "button", "name": "Gönder"}]}
        safe_snapshot = {"elements": [{"ref": 6, "role": "button", "name": "Kapat"}]}

        first = registry.needs_confirm(click, {"ref": 5}, risky_snapshot)
        second = registry.needs_confirm(click, {"ref": 5}, risky_snapshot)
        if first is None:
            problems.append("riskli etiketli ('Gönder') click için onay istenmedi")
        elif first != second:
            problems.append("riskli click kapısı deterministik değil (aynı girdi, farklı sonuç)")
        else:
            notes.append(f"riskli etiketli click -> onay: {first}")

        if registry.needs_confirm(click, {"ref": 6}, safe_snapshot) is not None:
            problems.append("zararsız etiketli ('Kapat') click için gereksiz onay istendi")
        else:
            notes.append("zararsız etiketli click -> None")

        orphan = registry.needs_confirm(click, {"ref": 999}, risky_snapshot)
        if orphan is None:
            warnings.append("snapshot'ta olmayan ref için onay istenmiyor (beklenen: dolu)")
        else:
            notes.append(f"snapshot'ta olmayan ref -> onay: {orphan}")

    return _summary(problems, warnings, notes)


# ---------------------------------------------------------------------------
# 7. Fixture denetimi
# ---------------------------------------------------------------------------
def check_fixture() -> Result:
    if not FIXTURE_PATH.is_file():
        return Result("FAIL", [f"fixture bulunamadı: {FIXTURE_PATH}"])

    try:
        from backend.trace import TraceStep
    except Exception as exc:
        return Result("FAIL", [f"backend.trace içe aktarılamadı: {exc!r}"])

    problems: list[str] = []
    notes: list[str] = []
    counts: dict[str, int] = {}
    trace_steps = 0
    finals_per_request: dict[str, int] = {}

    try:
        importlib.import_module("backend.tools.browser")
        from backend.tools import registry as tool_registry
    except Exception as exc:  # pragma: no cover - reported by check 1 instead
        tool_registry = None  # type: ignore[assignment]
        notes.append(f"tool kaydı okunamadı, args denetimi atlandı: {exc!r}")

    text = FIXTURE_PATH.read_text(encoding="utf-8")
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"satır {number}: geçersiz JSON ({exc.msg})")
            continue
        if not isinstance(frame, dict):
            problems.append(f"satır {number}: JSON nesnesi değil")
            continue
        frame_type = frame.get("type")
        if frame_type not in ALLOWED_FRAME_TYPES:
            problems.append(f"satır {number}: izinli olmayan type {frame_type!r}")
            continue
        counts[str(frame_type)] = counts.get(str(frame_type), 0) + 1

        if frame_type == "trace":
            step = frame.get("step")
            if not isinstance(step, dict):
                problems.append(f"satır {number}: trace satırında 'step' nesnesi yok")
                continue
            try:
                TraceStep.model_validate(step)
                trace_steps += 1
            except Exception as exc:
                problems.append(f"satır {number}: TraceStep doğrulaması başarısız -> {exc.__class__.__name__}")

        if frame_type == "final":
            request_id = str(frame.get("request_id") or "?")
            finals_per_request[request_id] = finals_per_request.get(request_id, 0) + 1

        if frame_type == "tool_call" and tool_registry is not None:
            tool = tool_registry.get(str(frame.get("name") or ""))
            if tool is None:
                name = str(frame.get("name") or "")
                if name in EXPECTED_LATER:
                    notes.append(f"satır {number}: {name} henüz kayıtlı değil (K4/K5)")
                else:
                    problems.append(f"satır {number}: tool_call kayıtlı olmayan araç: {name!r}")
            else:
                args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
                declared = set((tool.parameters.get("properties") or {}).keys())
                undeclared = sorted(set(args) - declared)
                if undeclared:
                    problems.append(
                        f"satır {number}: {tool.name} args şemada tanımsız alan içeriyor: {', '.join(undeclared)}"
                    )
                missing = [key for key in tool.parameters.get("required", []) if key not in args]
                if missing:
                    problems.append(
                        f"satır {number}: {tool.name} zorunlu args eksik: {', '.join(missing)}"
                    )

    for request_id, total in finals_per_request.items():
        if total > 1:
            problems.append(
                f"aynı request_id ({request_id}) için {total} 'final' olayı var "
                "(bir istek = bir final)"
            )

    for required in ("need_confirm", "final"):
        if counts.get(required, 0) == 0:
            problems.append(f"fixture'da hiç {required!r} olayı yok")

    notes.append("olay dağılımı: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(boş)"))
    notes.append(f"TraceStep ile doğrulanan adım: {trace_steps}")
    notes.append(
        "request_id başına final: "
        + (", ".join(f"{k}={v}" for k, v in sorted(finals_per_request.items())) or "(yok)")
    )
    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# 8. Ajan modülleri ve imzaları
# ---------------------------------------------------------------------------
def _import_origin(exc: BaseException) -> str:
    """Project içindeki en derin traceback karesi (import hatasının kaynağı)."""

    origin = ""
    tb = exc.__traceback__
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        if str(PROJECT_ROOT) in filename and "__pycache__" not in filename:
            origin = f"{Path(filename).name}:{tb.tb_lineno}"
        tb = tb.tb_next
    return origin


def check_agents() -> Result:
    problems: list[str] = []
    notes: list[str] = []

    for module_name, class_name, method_name, expected in AGENT_CONTRACTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            origin = _import_origin(exc)
            hint = f" [gerçek kaynak: {origin}]" if origin else ""
            problems.append(f"{module_name} içe aktarılamadı: {exc!r}{hint}")
            continue

        cls = getattr(module, class_name, None)
        if cls is None:
            problems.append(f"{module_name}: {class_name} sınıfı yok")
            continue

        method = getattr(cls, method_name, None)
        if method is None:
            problems.append(f"{class_name}: {method_name}() metodu yok")
            continue

        if not inspect.iscoroutinefunction(method):
            problems.append(f"{class_name}.{method_name} async değil")

        try:
            params = set(inspect.signature(method).parameters)
        except (TypeError, ValueError) as exc:
            problems.append(f"{class_name}.{method_name}: imza okunamadı ({exc!r})")
            continue

        missing = sorted(expected - params)
        if missing:
            problems.append(f"{class_name}.{method_name}: eksik parametre -> {', '.join(missing)}")
        else:
            notes.append(f"{class_name}.{method_name} imzası uygun ({', '.join(sorted(expected))})")

    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# 9. main.py route'ları
# ---------------------------------------------------------------------------
def check_main_routes() -> Result:
    try:
        main = importlib.import_module("backend.main")
    except Exception as exc:
        return Result("FAIL", [f"backend.main içe aktarılamadı: {exc!r}"])

    app = getattr(main, "app", None)
    if app is None:
        return Result("FAIL", ["backend.main içinde `app` nesnesi yok"])

    health_ok = False
    ws_ok = False
    paths: list[str] = []
    for route in getattr(app, "routes", []):
        path = str(getattr(route, "path", ""))
        paths.append(path)
        methods = getattr(route, "methods", None) or set()
        if path == "/health" and "GET" in methods:
            health_ok = True
        if path == "/ws" and "websocket" in route.__class__.__name__.lower():
            ws_ok = True

    problems: list[str] = []
    if not health_ok:
        problems.append("GET /health route'u bulunamadı")
    if not ws_ok:
        problems.append("WS /ws route'u bulunamadı")

    notes = [
        f"kayıtlı route: {', '.join(sorted(paths))}",
        f"/health GET: {'var' if health_ok else 'yok'}, /ws WS: {'var' if ws_ok else 'yok'}",
    ]
    return _summary(problems, [], notes)


# ---------------------------------------------------------------------------
# Çalıştırma
# ---------------------------------------------------------------------------
CHECKS: tuple[tuple[str, Callable[[], Result]], ...] = (
    ("browser.* tool kaydı (8 adet)", check_browser_registration),
    ("tool kayıt sözleşmesi (namespace, şema, risk, Türkçe açıklama)", check_tool_records),
    ("llm_name / from_llm_name round-trip", check_name_roundtrip),
    ("inject_why: kopya güvenliği + zorunlu 'why'", check_inject_why),
    ("ROUTE_TOOLS tutarlılığı (K4/K5 eksikleri uyarı)", check_route_tools),
    ("onay kapısı determinizmi (needs_confirm)", check_confirm_gate),
    ("fixture: shared/fixtures/session_example.jsonl", check_fixture),
    ("ajan modülleri ve zorunlu imzalar", check_agents),
    ("backend.main route'ları (/health, /ws)", check_main_routes),
)


def _configure_output() -> None:
    """Windows konsolunda Türkçe karakterler bozulmasın."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - exotic stream
            pass


def _configure_logging() -> None:
    """Kütüphane logları raporu kirletmesin (backend.main basicConfig çağırıyor)."""

    logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()], force=True)


def main() -> int:
    _configure_output()
    _configure_logging()

    print("=" * 72)
    print("EVRE Asistan - statik sozlesme denetimi (LLM'siz)")
    print(f"Proje koku: {PROJECT_ROOT}")
    print("=" * 72)
    print()

    reporter = Reporter()
    for title, check in CHECKS:
        reporter.run(title, check)

    print("=" * 72)
    if reporter.failed:
        print("Basarisiz denetimler:")
        for title in reporter.failed:
            print(f"  - {title}")
    print(f"SONUC: {reporter.blockers} blocker, {reporter.warnings} uyari")
    print("=" * 72)
    return 1 if reporter.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
