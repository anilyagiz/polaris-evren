"""/health polling and the tray's status model (pure parts are unit tested)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

TOOLTIP_MAX = 120  # Windows NOTIFYICONDATA tooltip limit is 128 incl. NUL

RUNNING = "running"      # gold
NO_KEY = "nokey"         # red: server up, LLM key missing
STARTING = "starting"    # grey
STOPPED = "stopped"      # grey
ERROR = "error"          # red

ICON_COLOR = {RUNNING: "gold", NO_KEY: "red", STARTING: "grey", STOPPED: "grey", ERROR: "red"}


@dataclass(frozen=True)
class HealthInfo:
    ok: bool
    model: str = ""
    llm_configured: bool = True
    version: str = ""
    stt_ready: bool | None = None
    polaris: bool = False  # payload looks like Polaris' /health


def parse_health(payload: Any) -> HealthInfo:
    """Interpret a decoded /health JSON body. Unknown/missing fields are tolerated."""

    if not isinstance(payload, dict):
        return HealthInfo(ok=False)
    polaris = "ok" in payload and ("llm_model" in payload or "llm_configured" in payload)
    stt = payload.get("stt_ready")
    return HealthInfo(
        ok=payload.get("ok") is True,
        model=str(payload.get("llm_model") or ""),
        llm_configured=bool(payload.get("llm_configured", True)),
        version=str(payload.get("version") or ""),
        stt_ready=stt if isinstance(stt, bool) else None,
        polaris=polaris,
    )


def fetch_health(url: str, timeout: float = 2.0) -> HealthInfo | None:
    """GET /health. None = nothing answered; HealthInfo(ok=False) = answered but not healthy."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback URL
            body = resp.read(65536)
    except urllib.error.HTTPError:
        return HealthInfo(ok=False)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return parse_health(json.loads(body.decode("utf-8", errors="replace")))
    except ValueError:
        return HealthInfo(ok=False)


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """True when something accepts TCP connections on host:port."""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class Status:
    state: str
    tooltip: str
    detail: str  # the disabled "Durum" menu line

    @property
    def color(self) -> str:
        return ICON_COLOR.get(self.state, "grey")


def _clip(text: str) -> str:
    return text if len(text) <= TOOLTIP_MAX else text[: TOOLTIP_MAX - 1] + "…"


def compute_status(
    health: HealthInfo | None,
    *,
    process_alive: bool,
    wanted_running: bool,
    error: str = "",
    port: int | None = None,
) -> Status:
    """Combine the latest /health result with what the tray knows about its child."""

    where = f" · port {port}" if port else ""
    if health is not None and health.ok:
        if not health.llm_configured:
            return Status(NO_KEY, _clip("Polaris · LLM anahtarı eksik"),
                          f"Durum: LLM anahtarı eksik (backend\\.env){where}")
        model = health.model or "?"
        return Status(RUNNING, _clip(f"Polaris · çalışıyor · model {model}"),
                      f"Durum: çalışıyor · model {model}{where}")
    if error:
        return Status(ERROR, _clip(f"Polaris · hata · {error}"), f"Durum: hata — {error}")
    if wanted_running and process_alive:
        return Status(STARTING, "Polaris · başlıyor…", f"Durum: başlıyor…{where}")
    if wanted_running and not process_alive:
        return Status(ERROR, "Polaris · sunucu kapandı (kayıtlara bak)",
                      "Durum: sunucu beklenmedik şekilde kapandı")
    return Status(STOPPED, "Polaris · durdu", "Durum: durdu")
