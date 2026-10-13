"""Process management: fake LLM (in-process) + real backend subprocesses."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from .fake_llm import FakeLLMServer, free_port

ROOT = Path(__file__).resolve().parents[2]


class BackendProc:
    def __init__(self, name: str, fake: FakeLLMServer, workdir: Path, env_extra: dict[str, str]) -> None:
        self.name = name
        self.port = free_port()
        self.log_path = workdir / f"backend-{name}.log"
        self.db_path = workdir / f"{name}.db"
        self.env_extra = env_extra
        self.fake = fake
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_fh: Any = None

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def start(self, timeout: float = 90.0) -> None:
        env = dict(os.environ)
        env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "E2E_PORT": str(self.port),
                "E2E_BRAVE_URL": f"{self.fake.base_url}/brave/res/v1/web/search",
                "DEEPSEEK_API_KEY": "fake-key",
                "DEEPSEEK_BASE_URL": self.fake.base_url,
                "LLM_MODEL": "fake-llm",
                "LLM_TIMEOUT_S": "20",
                "BRAVE_API_KEY": "fake-brave",
                "ASSISTANT_DB": str(self.db_path),
                "DB_PATH": str(self.db_path),
                "TIMEZONE": "Europe/Istanbul",
                "CUA_ENABLED": "false",
                "DESKTOP_FAKE": "",
                "TOOL_TIMEOUT_S": "3",
                "CONFIRM_TIMEOUT_S": "3",
                "REMINDER_POLL_S": "5",
                "HOST": "127.0.0.1",
                "PORT": str(self.port),
            }
        )
        env.update(self.env_extra)
        self._log_fh = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "e2e" / "backend_launcher.py")],
            cwd=str(ROOT),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend '{self.name}' exited early (code {self.proc.returncode}):\n{self.log_tail(40)}")
            try:
                if httpx.get(f"{self.http}/health", timeout=1.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"backend '{self.name}' did not become healthy in {timeout}s:\n{self.log_tail(40)}")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_fh is not None:
            self._log_fh.close()

    def log_tail(self, lines: int = 30) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(no log)"
        return "\n".join(text.splitlines()[-lines:])

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


class Stack:
    """Fake LLM + lazily started backends ("main" = CUA off, "cua" = CUA on + fake desktop)."""

    PROFILES: dict[str, dict[str, str]] = {
        "main": {},
        "cua": {"CUA_ENABLED": "true", "DESKTOP_FAKE": "1", "DESKTOP_PARSER": "ocr"},
    }

    def __init__(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="polaris-e2e-"))
        self.fake = FakeLLMServer()
        self.backends: dict[str, BackendProc] = {}
        self._started = False

    def start(self) -> None:
        if not self._started:
            self.fake.start()
            self._started = True

    def backend(self, profile: str = "main") -> BackendProc:
        self.start()
        if profile not in self.backends:
            proc = BackendProc(profile, self.fake, self.workdir, self.PROFILES[profile])
            self.backends[profile] = proc
            proc.start()
        elif not self.backends[profile].alive():
            raise RuntimeError(f"backend '{profile}' died:\n{self.backends[profile].log_tail(40)}")
        return self.backends[profile]

    def stop(self) -> None:
        for proc in self.backends.values():
            proc.stop()
        if self._started:
            self.fake.stop()
