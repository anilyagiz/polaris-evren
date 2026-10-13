"""Masaüstü kontrolü (CUA) from the panel: one-click install + live on/off.  Owner: K3.

Endpoints (same loopback + origin guard as ``/settings/llm``):

* ``GET  /settings/desktop``          readiness checklist + install job state
* ``POST /settings/desktop/install``  background job: Python packages, Docker check, image pull
* ``PUT  /settings/desktop``          ``{"enabled": bool}`` -> ``CUA_ENABLED`` in backend/.env, applied live

Enabling registers the ``desktop.*`` tools in the registry right away; the
router and the agents read ``get_settings().cua_enabled`` on every request, so
the ``desktop_task`` route follows without a restart.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, settings_api
from .config import BACKEND_DIR, PROJECT_DIR, get_settings

logger = logging.getLogger(__name__)

IMAGE_NAME = "ghcr.io/trycua/linux:24.04"
DOCKER_URL = "https://www.docker.com/products/docker-desktop/"
REQUIREMENTS_PATH: Path = BACKEND_DIR / "requirements-cua.txt"
REQUIRED_MODULES: tuple[str, ...] = ("cua_sandbox", "rapidocr")

DOCKER_PROBE_TIMEOUT_S = 12.0
STATUS_TTL_S = 4.0
PIP_TIMEOUT_S = 1800.0
PULL_TIMEOUT_S = 3600.0
PIP_ATTEMPTS = 3
PIP_RETRY_DELAY_S = 10

#: Docker CLI locations when it is not on PATH (Docker Desktop, Rancher Desktop).
_DOCKER_FALLBACKS = (
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Rancher Desktop\resources\resources\win32\bin\docker.exe",
)

#: Called after a live enable (main.py uses it to mount /shots/{name}).
on_enabled: Callable[[], None] | None = None

MSG_DOCKER_MISSING = (
    f"Docker Desktop kurulu değil. Önce Docker Desktop'ı indirip kur ({DOCKER_URL}), açıp tekrar dene."
)
MSG_DOCKER_STOPPED = "Docker Desktop çalışmıyor, açıp tekrar dene. Sağ altta 'Engine running' yazana kadar bekle."
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


# ---------------------------------------------------------------------------
# Detection (module-level so tests can monkeypatch each probe)
# ---------------------------------------------------------------------------
def packages_installed() -> bool:
    importlib.invalidate_caches()
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def docker_path() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in _DOCKER_FALLBACKS:
        if os.path.isfile(candidate):
            return candidate
    return None


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )


def docker_running(docker: str) -> bool:
    try:
        return _run([docker, "info", "--format", "{{.ServerVersion}}"], DOCKER_PROBE_TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def image_present(docker: str) -> bool:
    try:
        return _run([docker, "image", "inspect", IMAGE_NAME, "--format", "{{.Id}}"], DOCKER_PROBE_TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_uv() -> str | None:
    """The uv.exe the installer ships (scripts/bootstrap.ps1), else one on PATH."""

    for candidate in (PROJECT_DIR / "tools" / "uv" / "uv.exe", PROJECT_DIR / "tools" / "uv.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("uv")


def probe() -> dict[str, Any]:
    """Blocking readiness probe (runs in a worker thread)."""

    pkgs = packages_installed()
    docker = docker_path()
    running = bool(docker) and docker_running(docker)  # type: ignore[arg-type]
    image = running and image_present(docker)  # type: ignore[arg-type]
    return {
        "packages_installed": pkgs,
        "docker_installed": bool(docker),
        "docker_running": running,
        "image_present": image,
    }


_probe_cache: tuple[float, dict[str, Any]] | None = None
_probe_lock = threading.Lock()


def cached_probe(force: bool = False) -> dict[str, Any]:
    global _probe_cache
    with _probe_lock:
        now = time.monotonic()
        if not force and _probe_cache and now - _probe_cache[0] < STATUS_TTL_S:
            return dict(_probe_cache[1])
        result = probe()
        _probe_cache = (time.monotonic(), result)
        return dict(result)


def _invalidate_probe() -> None:
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def _ready(checks: dict[str, Any]) -> bool:
    return all(checks[k] for k in ("packages_installed", "docker_installed", "docker_running", "image_present"))


# ---------------------------------------------------------------------------
# Install job (one at a time, in a daemon thread)
# ---------------------------------------------------------------------------
class InstallJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.installing = False
        self.progress: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "installing": self.installing,
                "install_progress": dict(self.progress) if self.progress else None,
                "last_error": self.last_error,
            }

    def set_progress(self, step: str, message: str, percent: int | None = None) -> None:
        with self.lock:
            self.progress = {"step": step, "message": message, "percent": percent}

    def start(self, enable_after: bool) -> bool:
        with self.lock:
            if self.installing:
                return False
            self.installing = True
            self.last_error = None
            self.progress = {"step": "start", "message": "Kurulum başlıyor…", "percent": 0}
        thread = threading.Thread(target=self._run, args=(enable_after,), name="desktop-install", daemon=True)
        self.thread = thread
        thread.start()
        return True

    def _run(self, enable_after: bool) -> None:
        try:
            run_install(self)
            if enable_after:
                set_enabled(True)
            self.set_progress("done", "Masaüstü kontrolü hazır.", 100)
        except InstallError as exc:
            with self.lock:
                self.last_error = str(exc)
                self.progress = None
            logger.warning("Masaüstü kurulumu başarısız: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Masaüstü kurulumu beklenmeyen hata")
            with self.lock:
                self.last_error = f"Kurulum beklenmedik bir hatayla durdu ({type(exc).__name__}). Tekrar dene."
                self.progress = None
        finally:
            _invalidate_probe()
            with self.lock:
                self.installing = False


class InstallError(Exception):
    """Turkish, user-facing reason the install stopped."""


job = InstallJob()


def _popen(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=_NO_WINDOW,
    )


def _stream(proc: subprocess.Popen[str], on_line: Callable[[str], None], timeout: float) -> tuple[int, list[str]]:
    """Feed every output line to ``on_line``; returns (returncode, last lines)."""

    tail: list[str] = []
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if line:
            tail = (tail + [line])[-15:]
            on_line(line)
        if time.monotonic() > deadline:
            proc.kill()
            raise InstallError("İşlem çok uzun sürdü ve durduruldu. İnternet bağlantını kontrol edip tekrar dene.")
    return proc.wait(), tail


def install_command() -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    uv = find_uv()
    if uv:
        # copy: hardlinks into site-packages trip "os error 32" (files locked by antivirus).
        env.update({"UV_NO_CONFIG": "1", "UV_HTTP_TIMEOUT": "120", "UV_NO_PROGRESS": "1", "UV_LINK_MODE": "copy"})
        runtime = PROJECT_DIR / "runtime"
        if (runtime / "uv").is_dir():
            env["UV_CACHE_DIR"] = str(runtime / "uv" / "uv-cache")
        elif runtime.is_dir():
            env["UV_CACHE_DIR"] = str(runtime / "uv-cache")
        return [uv, "pip", "install", "--python", sys.executable, "-r", str(REQUIREMENTS_PATH)], env
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS_PATH)], env


def _network_hint(lines: list[str]) -> str:
    text = " ".join(lines).lower()
    if any(k in text for k in ("connect", "timed out", "timeout", "dns", "network", "resolve", "ssl")):
        return " İnternet bağlantını kontrol edip tekrar dene."
    if "no space" in text or "disk" in text:
        return " Diskte yer aç (yaklaşık 6 GB gerekir) ve tekrar dene."
    return " Tekrar dene; sorun sürerse docs/cua-setup.md dosyasına bak."


_LOCK_MARKERS = ("os error 32", "being used by another process", "başka bir işlem tarafından kullanıl")


def _is_lock_error(lines: list[str]) -> bool:
    text = " ".join(lines).lower()
    return any(marker in text for marker in _LOCK_MARKERS)


def install_packages(job: InstallJob) -> None:
    cmd, env = install_command()
    tool = "uv" if Path(cmd[0]).stem.lower() == "uv" else "pip"
    job.set_progress("packages", f"Python paketleri kuruluyor ({tool}: cua, OCR)… Bu birkaç dakika sürebilir.", None)
    logger.info("Masaüstü paketleri kuruluyor (%s)", tool)

    def on_line(line: str) -> None:
        short = line.strip()
        if short.lower().startswith(("downloading", "collecting", "installing", "prepared", "installed", " + ")):
            job.set_progress("packages", f"Python paketleri kuruluyor: {short[:90]}", None)

    for attempt in range(1, PIP_ATTEMPTS + 1):
        try:
            code, tail = _stream(_popen(cmd, env), on_line, PIP_TIMEOUT_S)
        except OSError:
            raise InstallError("Paket yükleyici başlatılamadı (uv/pip bulunamadı). Polaris'i yeniden kurmayı dene.") from None
        if code == 0 or not _is_lock_error(tail) or attempt == PIP_ATTEMPTS:
            break
        logger.info("Paket kurulumu dosya kilidine takıldı, %d. deneme %d sn sonra", attempt + 1, PIP_RETRY_DELAY_S)
        job.set_progress("packages", f"Bir dosya başka bir işlem tarafından kullanılıyor; tekrar deneniyor ({attempt + 1}/{PIP_ATTEMPTS})…", None)
        time.sleep(PIP_RETRY_DELAY_S)
    if code != 0 and _is_lock_error(tail):
        raise InstallError(
            "Python paketleri kurulamadı: bazı dosyalar başka bir işlem (ör. antivirüs) tarafından kilitli. "
            "Biraz bekleyip tekrar dene."
        )
    if code != 0:
        logger.warning("Paket kurulumu çıkış kodu %s: %s", code, " | ".join(tail[-5:]))
        raise InstallError("Python paketleri kurulamadı." + _network_hint(tail))
    if not packages_installed():
        raise InstallError("Paketler kuruldu ama yüklenemedi (cua_sandbox/rapidocr). Polaris'i yeniden başlatıp tekrar dene.")


_LAYER_RE = re.compile(r"^([0-9a-f]{12}):\s*(.+)$")
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


class PullProgress:
    """Layer-based percent from non-TTY `docker pull` output."""

    def __init__(self) -> None:
        self.layers: dict[str, str] = {}

    def feed(self, line: str) -> int | None:
        match = _LAYER_RE.match(line.strip())
        if match:
            layer, status = match.groups()
            status = status.lower()
            if "pull complete" in status or "already exists" in status:
                self.layers[layer] = "done"
            elif any(k in status for k in ("download complete", "extracting", "verifying")):
                if self.layers.get(layer) != "done":
                    self.layers[layer] = "downloaded"
            else:
                self.layers.setdefault(layer, "pending")
        else:
            pct = _PCT_RE.search(line)
            if pct and not self.layers:
                return max(0, min(100, int(float(pct.group(1)))))
        if not self.layers:
            return None
        score = sum(1.0 if s == "done" else 0.6 if s == "downloaded" else 0.0 for s in self.layers.values())
        return int(score * 100 / len(self.layers))


def pull_image(job: InstallJob, docker: str) -> None:
    job.set_progress("image", "Masaüstü imajı indiriliyor (~1,3 GB)…", 0)
    logger.info("docker pull %s", IMAGE_NAME)
    progress = PullProgress()

    def on_line(line: str) -> None:
        pct = progress.feed(line)
        msg = "Masaüstü imajı indiriliyor (~1,3 GB)…"
        if pct is not None:
            msg = f"Masaüstü imajı indiriliyor (~1,3 GB)… %{pct}"
        job.set_progress("image", msg, pct)

    try:
        code, tail = _stream(_popen([docker, "pull", IMAGE_NAME]), on_line, PULL_TIMEOUT_S)
    except OSError:
        raise InstallError(MSG_DOCKER_MISSING) from None
    if code != 0:
        logger.warning("docker pull çıkış kodu %s: %s", code, " | ".join(tail[-5:]))
        text = " ".join(tail).lower()
        if "daemon" in text or "pipe" in text or "connect" in text and "docker" in text:
            raise InstallError(MSG_DOCKER_STOPPED)
        raise InstallError("Masaüstü imajı indirilemedi." + _network_hint(tail))


def run_install(job: InstallJob) -> None:
    """Idempotent: every step is skipped when it is already done."""

    job.set_progress("packages", "Python paketleri kontrol ediliyor…", 5)
    if not packages_installed():
        install_packages(job)
    job.set_progress("docker", "Docker kontrol ediliyor…", None)
    docker = docker_path()
    if not docker:
        raise InstallError(MSG_DOCKER_MISSING)
    if not docker_running(docker):
        raise InstallError(MSG_DOCKER_STOPPED)
    job.set_progress("image", "Masaüstü imajı kontrol ediliyor…", None)
    if not image_present(docker):
        pull_image(job, docker)
        if not image_present(docker):
            raise InstallError("İmaj indirildi görünüyor ama bulunamadı. Docker Desktop'ı yeniden başlatıp tekrar dene.")


# ---------------------------------------------------------------------------
# Live enable / disable
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    return get_settings().cua_enabled


def set_enabled(enabled: bool) -> None:
    value = "true" if enabled else "false"
    with settings_api._write_lock:
        settings_api._write_env({"CUA_ENABLED": value})
        os.environ["CUA_ENABLED"] = value
        config.reload_settings()
    from .tools import desktop

    if enabled:
        desktop.register_desktop_tools()
        if on_enabled is not None:
            try:
                on_enabled()
            except Exception:  # pragma: no cover - defensive
                logger.exception("on_enabled kancası başarısız")
    else:
        desktop.unregister_desktop_tools()
    logger.info("Masaüstü kontrolü %s", "açıldı" if enabled else "kapatıldı")


def _missing_text(checks: dict[str, Any]) -> str:
    if not checks["docker_installed"]:
        return MSG_DOCKER_MISSING
    if not checks["docker_running"]:
        return MSG_DOCKER_STOPPED
    parts = []
    if not checks["packages_installed"]:
        parts.append("Python paketleri")
    if not checks["image_present"]:
        parts.append("masaüstü imajı")
    return f"Önce kurulumu tamamla: {' ve '.join(parts)} eksik. 'Kur ve aç' düğmesini kullan."


async def status_payload(force: bool = False) -> dict[str, Any]:
    checks = await asyncio.to_thread(cached_probe, force)
    return {
        "enabled": is_enabled(),
        **checks,
        "image_name": IMAGE_NAME,
        "docker_url": DOCKER_URL,
        **job.snapshot(),
        "ready": _ready(checks),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(settings_api._guard)])


@router.get("/desktop")
async def get_desktop_settings(refresh: bool = False) -> dict[str, Any]:
    return await status_payload(force=refresh)


@router.post("/desktop/install")
async def install_desktop(request: Request) -> dict[str, Any]:
    body = await settings_api._json_body(request)
    enable = body.get("enable", False)
    if not isinstance(enable, bool):
        raise HTTPException(422, "enable true/false olmalı.")
    started = job.start(enable_after=enable)
    if started:
        logger.info("Masaüstü kurulumu başlatıldı (sonra aç: %s)", enable)
    payload = await status_payload()
    payload["started"] = started
    return payload


@router.put("/desktop")
async def put_desktop_settings(request: Request) -> dict[str, Any]:
    body = await settings_api._json_body(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(422, "enabled true veya false olmalı.")
    if enabled:
        checks = await asyncio.to_thread(cached_probe, True)
        if not _ready(checks):
            raise HTTPException(409, _missing_text(checks))
    try:
        await asyncio.to_thread(set_enabled, enabled)
    except OSError as exc:
        logger.error("backend/.env yazılamadı: %s", type(exc).__name__)
        raise HTTPException(500, "Ayar dosyaya yazılamadı (backend/.env).") from None
    return await status_payload()
