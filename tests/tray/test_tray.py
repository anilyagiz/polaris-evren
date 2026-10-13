"""Unit tests for the Polaris tray (no GUI, no real backend)."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from tray import autostart as autostart_mod
from tray.app import READY_TOAST, Controller
from tray.autostart import RUN_KEY, VALUE_NAME, Autostart, launcher_command
from tray.backend_proc import (
    CREATE_NO_WINDOW, BackendProcess, build_command, build_env, python_for_backend,
)
from tray.health import (
    ERROR, NO_KEY, RUNNING, STARTING, STOPPED, TOOLTIP_MAX, HealthInfo, compute_status, parse_health,
)
from tray.paths import Paths, find_root
from tray.settings import BackendAddress, ensure_env_file, parse_env_text, resolve_address
from tray.single_instance import SingleInstance

REPO = Path(__file__).resolve().parents[2]


# -- health parsing --------------------------------------------------------------
def test_parse_health_full_payload():
    info = parse_health({"ok": True, "version": "0.1.0", "llm_model": "deepseek-chat",
                         "llm_configured": True, "stt_ready": False})
    assert info == HealthInfo(ok=True, model="deepseek-chat", llm_configured=True,
                              version="0.1.0", stt_ready=False, polaris=True)


@pytest.mark.parametrize("payload", [None, [], "ok", {"ok": "yes"}, {}])
def test_parse_health_garbage_is_not_ok(payload):
    assert parse_health(payload).ok is False


def test_parse_health_foreign_service_is_not_polaris():
    assert parse_health({"ok": True, "status": "up"}).polaris is False


def test_status_running_tooltip():
    s = compute_status(parse_health({"ok": True, "llm_model": "deepseek-chat", "llm_configured": True}),
                       process_alive=True, wanted_running=True, port=8000)
    assert s.state == RUNNING and s.color == "gold"
    assert s.tooltip == "Polaris · çalışıyor · model deepseek-chat"
    assert "8000" in s.detail


def test_status_missing_key():
    s = compute_status(parse_health({"ok": True, "llm_model": "x", "llm_configured": False}),
                       process_alive=True, wanted_running=True)
    assert s.state == NO_KEY and s.tooltip == "Polaris · LLM anahtarı eksik" and s.color == "red"


def test_status_stopped_starting_crashed_error():
    assert compute_status(None, process_alive=False, wanted_running=False).tooltip == "Polaris · durdu"
    assert compute_status(None, process_alive=False, wanted_running=False).color == "grey"
    assert compute_status(None, process_alive=True, wanted_running=True).state == STARTING
    assert compute_status(None, process_alive=False, wanted_running=True).state == ERROR
    assert compute_status(None, process_alive=False, wanted_running=False, error="port 8000 dolu").state == ERROR


def test_tooltip_is_clipped():
    s = compute_status(HealthInfo(ok=True, model="m" * 300), process_alive=True, wanted_running=True)
    assert len(s.tooltip) <= TOOLTIP_MAX


# -- settings ----------------------------------------------------------------------
def test_parse_env_text_handles_comments_quotes_export():
    values = parse_env_text('# c\nPORT=8010   # yorum\nexport HOST="0.0.0.0"\nX\nEMPTY=\n')
    assert values == {"PORT": "8010", "HOST": "0.0.0.0", "EMPTY": ""}


def test_resolve_address_env_overrides_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=8010\nHOST=0.0.0.0\n", encoding="utf-8")
    assert resolve_address(env_file, {}) == BackendAddress("0.0.0.0", 8010)
    assert resolve_address(env_file, {"PORT": "9001"}).port == 9001
    assert resolve_address(env_file, {}).health_url == "http://127.0.0.1:8010/health"


def test_resolve_address_defaults_and_bad_port(tmp_path):
    assert resolve_address(tmp_path / "missing.env", {}) == BackendAddress("127.0.0.1", 8765)
    assert resolve_address(tmp_path / "missing.env", {"PORT": "abc"}).port == 8765
    assert resolve_address(tmp_path / "missing.env", {"PORT": "70000"}).port == 8765


def test_ensure_env_file_copies_example(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    target = tmp_path / ".env"
    assert ensure_env_file(target, example) is True
    assert target.read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=\n"
    assert ensure_env_file(target, example) is False


# -- paths -------------------------------------------------------------------------
def test_find_root_walks_up_from_dist_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("POLARIS_ROOT", raising=False)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("")
    dist = tmp_path / "tray" / "dist"
    dist.mkdir(parents=True)
    assert find_root(dist) == tmp_path
    assert find_root(tmp_path) == tmp_path


def test_find_root_real_repo():
    assert find_root(REPO / "tray") == REPO


# -- command construction ------------------------------------------------------------
def _fake_paths(tmp_path: Path, with_main: bool) -> Paths:
    (tmp_path / "backend").mkdir(exist_ok=True)
    if with_main:
        (tmp_path / "backend" / "__main__.py").write_text("")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "pythonw.exe").write_text("")
    (scripts / "python.exe").write_text("")
    return Paths(tmp_path)


def test_build_command_prefers_python_m_backend(tmp_path):
    paths = _fake_paths(tmp_path, with_main=True)
    py = python_for_backend(paths)
    assert py.name == "pythonw.exe"
    cmd = build_command(py, BackendAddress("127.0.0.1", 8123), paths.has_backend_main)
    assert cmd == [str(py), "-m", "backend", "--host", "127.0.0.1", "--port", "8123"]


def test_build_command_uvicorn_fallback(tmp_path):
    paths = _fake_paths(tmp_path, with_main=False)
    cmd = build_command(python_for_backend(paths), BackendAddress("127.0.0.1", 8765), paths.has_backend_main)
    assert cmd[1:] == ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8765"]


def test_build_env_sets_port_and_utf8():
    env = build_env(BackendAddress("127.0.0.1", 8123), {"KEEP": "1"})
    assert env["PORT"] == "8123" and env["HOST"] == "127.0.0.1"
    assert env["PYTHONIOENCODING"] == "utf-8" and env["KEEP"] == "1"


class FakeProc:
    def __init__(self, hang: bool = False):
        self.pid = 4242
        self.hang = hang
        self.returncode = None
        self.calls: list[str] = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append("terminate")
        if not self.hang:
            self.returncode = 1

    def kill(self):
        self.calls.append("kill")
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("x", timeout)
        return self.returncode


def test_backend_process_hidden_window_and_log(tmp_path):
    paths = _fake_paths(tmp_path, with_main=True)
    seen = {}
    proc = FakeProc()

    def popen(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return proc

    bp = BackendProcess(paths, BackendAddress("127.0.0.1", 8123), popen=popen, use_job=False)
    bp.start()
    assert bp.is_alive()
    if os.name == "nt":
        assert seen["creationflags"] & CREATE_NO_WINDOW
    assert seen["cwd"] == str(tmp_path)
    assert seen["stderr"] == subprocess.STDOUT
    assert paths.backend_log.exists()
    assert seen["env"]["PORT"] == "8123"
    bp.stop()
    assert proc.calls == ["terminate"] and not bp.is_alive()


def test_backend_process_kills_after_grace(tmp_path):
    paths = _fake_paths(tmp_path, with_main=True)
    proc = FakeProc(hang=True)
    bp = BackendProcess(paths, BackendAddress("127.0.0.1", 1), popen=lambda *a, **k: proc, use_job=False)
    bp.start()
    bp.stop(grace_s=0.01)
    assert proc.calls == ["terminate", "kill"]


# -- controller ----------------------------------------------------------------------
class StubBackend:
    def __init__(self):
        self.alive = False
        self.starts = 0
        self.address = None

    def is_alive(self):
        return self.alive

    def start(self):
        self.starts += 1
        self.alive = True

    def stop(self):
        self.alive = False


def _controller(tmp_path, *, port_busy=False, health=None):
    alerts, toasts = [], []
    ctrl = Controller(Paths(tmp_path), address=BackendAddress("127.0.0.1", 8123), backend=StubBackend(),
                      fetch=lambda url: health, port_check=lambda h, p: port_busy,
                      alert=alerts.append, notify=toasts.append)
    return ctrl, alerts, toasts


def test_controller_port_busy_by_foreign_process(tmp_path):
    ctrl, alerts, _ = _controller(tmp_path, port_busy=True, health=None)
    ctrl.start()
    assert ctrl.backend.starts == 0
    assert alerts and "PORT=" in alerts[0] and "8123" in alerts[0]
    assert ctrl.poll().state == ERROR


def test_controller_attaches_to_existing_polaris(tmp_path):
    health = HealthInfo(ok=True, model="m", polaris=True)
    ctrl, alerts, toasts = _controller(tmp_path, port_busy=True, health=health)
    ctrl.start()
    assert ctrl.external and ctrl.backend.starts == 0 and not alerts
    assert ctrl.poll().state == RUNNING
    assert toasts == [READY_TOAST]


def test_controller_toast_only_once_and_stop(tmp_path):
    health = HealthInfo(ok=True, model="m", polaris=True)
    ctrl, _, toasts = _controller(tmp_path, health=health)
    ctrl.start()
    assert ctrl.backend.starts == 1
    ctrl.poll()
    ctrl.poll()
    assert toasts == [READY_TOAST]
    ctrl.toggle_running()
    assert not ctrl.is_running()
    assert ctrl.poll().state == STOPPED


# -- autostart (mock winreg) -----------------------------------------------------------
class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def OpenKey(self, root, path, reserved=0, access=0):
        assert (root, path) == ("HKCU", RUN_KEY)
        return FakeKey()

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        return self.OpenKey(root, path)

    def QueryValueEx(self, key, name):
        try:
            return self.values[(RUN_KEY, name)], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name) from None

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(RUN_KEY, name)] = value

    def DeleteValue(self, key, name):
        if (RUN_KEY, name) not in self.values:
            raise FileNotFoundError(name)
        del self.values[(RUN_KEY, name)]


def test_autostart_toggle_roundtrip():
    reg = FakeWinreg()
    auto = Autostart('"C:\\Polaris\\Polaris.exe"', winreg_module=reg)
    assert not auto.is_enabled()
    assert auto.toggle() is True
    assert reg.values[(RUN_KEY, VALUE_NAME)] == '"C:\\Polaris\\Polaris.exe"'
    assert auto.is_enabled() and auto.is_current()
    assert auto.toggle() is False
    assert not auto.is_enabled()
    auto.disable()  # idempotent


def test_autostart_detects_stale_entry():
    reg = FakeWinreg()
    reg.values[(RUN_KEY, VALUE_NAME)] = '"D:\\old\\Polaris.exe"'
    auto = Autostart('"C:\\new\\Polaris.exe"', winreg_module=reg)
    assert auto.is_enabled() and not auto.is_current()


def test_launcher_command_variants(tmp_path):
    paths = _fake_paths(tmp_path, with_main=True)
    assert launcher_command(paths, frozen=True, executable="C:\\X\\Polaris.exe") == '"C:\\X\\Polaris.exe"'
    cmd = launcher_command(paths, frozen=False)
    assert "pythonw.exe" in cmd and cmd.endswith('launch.pyw"')
    (tmp_path / "Polaris.exe").write_text("")
    assert launcher_command(paths, frozen=False) == f'"{tmp_path / "Polaris.exe"}"'


def test_autostart_module_uses_hkcu_run_key():
    assert autostart_mod.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"


# -- single instance ---------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="named mutex is Windows-only")
def test_single_instance_named_mutex():
    name = f"Local\\PolarisTrayTest-{uuid.uuid4()}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    third = SingleInstance(name)
    assert third.acquire() is True
    third.release()


def test_single_instance_lock_file(tmp_path):
    first = SingleInstance(lock_dir=tmp_path, use_mutex=False)
    second = SingleInstance(lock_dir=tmp_path, use_mutex=False)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_single_instance_stale_lock_is_reclaimed(tmp_path):
    (tmp_path / "polaris-tray.lock").write_text("999999999")
    guard = SingleInstance(lock_dir=tmp_path, use_mutex=False)
    assert guard.acquire() is True
    guard.release()


def test_icons_render_all_variants():
    from tray.icons import make_icon

    for color in ("gold", "grey", "red"):
        img = make_icon(color)
        assert img.size == (64, 64) and img.mode == "RGBA"
    assert make_icon("gold").getpixel((32, 12)) != make_icon("grey").getpixel((32, 12))


def test_controller_falls_back_to_next_free_port(tmp_path):
    busy = {8123, 8765}
    ctrl = Controller(Paths(tmp_path), address=BackendAddress("127.0.0.1", 8123), backend=StubBackend(),
                      fetch=lambda url: None, port_check=lambda h, p: p in busy,
                      alert=lambda text: None, notify=lambda text: None)
    ctrl.start()
    assert ctrl.backend.starts == 1 and ctrl.error == ""
    assert ctrl.address.port == 8766 and ctrl.backend.address.port == 8766
