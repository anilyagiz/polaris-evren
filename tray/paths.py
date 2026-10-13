"""Locate the Polaris install root and the files the tray needs.

Root = the folder that contains ``backend/``.  For a frozen ``Polaris.exe`` we
start at the exe's folder (repo root or release root) and walk up a few levels,
so running ``tray\\dist\\Polaris.exe`` straight from a dev checkout also works.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _looks_like_root(path: Path) -> bool:
    return (path / "backend").is_dir() and (
        (path / "backend" / "main.py").is_file() or (path / "backend" / "__main__.py").is_file()
    )


def find_root(start: Path | None = None, max_up: int = 4) -> Path:
    """Return the install root. Falls back to ``start`` when nothing matches."""

    override = os.environ.get("POLARIS_ROOT")
    if override and _looks_like_root(Path(override)):
        return Path(override).resolve()
    if start is None:
        if getattr(sys, "frozen", False):
            start = Path(sys.executable).resolve().parent
        else:
            start = Path(__file__).resolve().parent.parent
    current = start
    for _ in range(max_up + 1):
        if _looks_like_root(current):
            return current
        if current.parent == current:
            break
        current = current.parent
    return start


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def backend(self) -> Path:
        return self.root / "backend"

    @property
    def env_file(self) -> Path:
        return self.backend / ".env"

    @property
    def env_example(self) -> Path:
        return self.backend / ".env.example"

    @property
    def logs(self) -> Path:
        return self.backend / "data" / "logs"

    @property
    def backend_log(self) -> Path:
        return self.logs / "tray-backend.log"

    @property
    def tray_log(self) -> Path:
        return self.logs / "tray.log"

    @property
    def venv_scripts(self) -> Path:
        return self.root / ".venv" / "Scripts"

    @property
    def pythonw(self) -> Path:
        return self.venv_scripts / "pythonw.exe"

    @property
    def python(self) -> Path:
        return self.venv_scripts / "python.exe"

    @property
    def has_backend_main(self) -> bool:
        return (self.backend / "__main__.py").is_file()

    @property
    def extension_dist(self) -> Path:
        return self.root / "extension" / "dist"

    @property
    def tray_doc(self) -> Path:
        return self.root / "docs" / "TRAY.md"

    @property
    def launcher_pyw(self) -> Path:
        return self.root / "tray" / "launch.pyw"


def get_paths() -> Paths:
    return Paths(find_root())
