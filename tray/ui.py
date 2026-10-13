"""Native message boxes and shell helpers (no extra dependencies)."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("tray.ui")

MB_OK = 0x0
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30
MB_ICONERROR = 0x10
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000


def message_box(text: str, title: str = "Polaris", icon: int = MB_ICONINFORMATION,
                wait: bool = False) -> None:
    """Show a Windows message box; non-blocking by default (runs on its own thread)."""

    def _show() -> None:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK | icon | MB_SETFOREGROUND | MB_TOPMOST)
        except Exception as exc:  # pragma: no cover - non-Windows
            log.warning("message box gösterilemedi: %s | %s", exc, text)

    if wait:
        _show()
    else:
        threading.Thread(target=_show, daemon=True).start()


def open_path(path: Path) -> None:
    """Open a folder in Explorer / a file with its default app."""

    os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606


def open_in_notepad(path: Path) -> None:
    subprocess.Popen(["notepad.exe", str(path)], close_fds=True)  # noqa: S603,S607


def open_in_explorer(path: Path) -> None:
    subprocess.Popen(["explorer.exe", str(path)], close_fds=True)  # noqa: S603,S607


BRAVE_STEPS = (
    "Polaris eklentisini Brave'e yükleme:\n\n"
    "1) Brave'de adres çubuğuna  brave://extensions  yaz.\n"
    "2) Sağ üstten 'Geliştirici modu'nu aç.\n"
    "3) 'Paketlenmemiş uzantıyı yükle' → şu klasörü seç:\n"
    "    {dist}\n"
    "4) Araç çubuğundaki Polaris simgesine tıkla; yan panel açılır.\n"
    "5) Panel Ayarlar → Backend adresi:  {url}\n\n"
    "Eklenti güncellenince brave://extensions sayfasında 'Yenile'ye bas."
)

PORT_BUSY = (
    "Port {port} başka bir program tarafından kullanılıyor, Polaris sunucusu başlatılamadı.\n\n"
    "Çözüm:\n"
    "1) Tepsi menüsünden 'Ayarlar dosyasını aç' (backend\\.env).\n"
    "2) Şu satırı ekle/değiştir:  PORT=8010  (boş bir port).\n"
    "3) Kaydet ve menüden 'Sunucuyu yeniden başlat'.\n"
    "4) Brave paneli → Ayarlar → Backend adresi:  http://localhost:8010"
)
