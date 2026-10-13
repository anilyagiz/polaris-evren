"""'Windows açılışında başlat': HKCU\\...\\CurrentVersion\\Run\\Polaris."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .paths import Paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Polaris"


def _default_winreg() -> Any:
    import winreg  # Windows only

    return winreg


def launcher_command(paths: Paths, frozen: bool | None = None, executable: str | None = None) -> str:
    """What Windows should run at logon: Polaris.exe, else pythonw + launch.pyw."""

    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return f'"{Path(executable or sys.executable)}"'
    exe = paths.root / "Polaris.exe"
    if exe.exists():
        return f'"{exe}"'
    pythonw = paths.pythonw if paths.pythonw.exists() else Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{paths.launcher_pyw}"'


class Autostart:
    def __init__(self, command: str, winreg_module: Any = None) -> None:
        self.command = command
        self._winreg = winreg_module

    @property
    def reg(self) -> Any:
        if self._winreg is None:
            self._winreg = _default_winreg()
        return self._winreg

    def current_value(self) -> str | None:
        reg = self.reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                value, _ = reg.QueryValueEx(key, VALUE_NAME)
                return str(value)
        except OSError:
            return None

    def is_enabled(self) -> bool:
        return self.current_value() is not None

    def is_current(self) -> bool:
        """Enabled *and* pointing at this launcher (not a moved/old install)."""

        value = self.current_value()
        return value is not None and value.strip().lower() == self.command.strip().lower()

    def enable(self) -> None:
        reg = self.reg
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, self.command)

    def disable(self) -> None:
        reg = self.reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass

    def toggle(self) -> bool:
        """Flip the state; returns the new state."""

        if self.is_enabled():
            self.disable()
            return False
        self.enable()
        return True
