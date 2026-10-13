"""HOST/PORT for the backend, read the same way backend/config.py does.

Process environment wins over ``backend/.env`` (python-dotenv's default), and
the default port is 8000.  Only these two keys are read; secrets are ignored.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def parse_env_text(text: str) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, `export ` prefix, quotes, ` #` comments."""

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            for marker in (" #", "\t#"):
                idx = value.find(marker)
                if idx != -1:
                    value = value[:idx].rstrip()
        if key:
            values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    try:
        return parse_env_text(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return {}


@dataclass(frozen=True)
class BackendAddress:
    host: str
    port: int

    @property
    def probe_host(self) -> str:
        """Address to poll: wildcard binds are reachable on loopback."""

        return "127.0.0.1" if self.host in {"0.0.0.0", "", "::", "localhost"} else self.host

    @property
    def base_url(self) -> str:
        return f"http://{self.probe_host}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"


def resolve_address(env_file: Path, environ: Mapping[str, str] | None = None) -> BackendAddress:
    env = os.environ if environ is None else environ
    file_values = read_env_file(env_file)

    def pick(name: str) -> str:
        value = (env.get(name) or "").strip()
        return value or file_values.get(name, "").strip()

    host = pick("HOST") or DEFAULT_HOST
    try:
        port = int(pick("PORT") or DEFAULT_PORT)
        if not 0 < port < 65536:
            raise ValueError
    except ValueError:
        port = DEFAULT_PORT
    return BackendAddress(host=host, port=port)


def ensure_env_file(env_file: Path, example: Path) -> bool:
    """Create backend/.env from .env.example when missing. Returns True if created."""

    if env_file.exists():
        return False
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if example.exists():
        shutil.copyfile(example, env_file)
    else:
        env_file.write_text("DEEPSEEK_API_KEY=\nLLM_MODEL=deepseek-chat\nPORT=8000\n", encoding="utf-8")
    return True
