"""/settings/desktop: one-click CUA install + live on/off (never touches the real backend/.env)."""
from __future__ import annotations

import io
import subprocess
import time

import pytest

from backend.trace import TraceStore

EXT = "chrome-extension://" + "a" * 32
DESKTOP_TOOLS = {"desktop.snapshot", "desktop.click", "desktop.type", "desktop.key", "desktop.scroll"}
ENV_TEXT = "# Polaris\r\nLLM_MODEL=gemma-4-31b\r\n# --- Masaüstü ---\r\nCUA_ENABLED=false\r\nTIMEZONE=Europe/Istanbul\r\n"
ALL_OK = {"packages_installed": True, "docker_installed": True, "docker_running": True, "image_present": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import config, main, settings_api, settings_desktop
    from backend.tools import desktop

    env_file = tmp_path / ".env"
    env_file.write_bytes(ENV_TEXT.encode("utf-8"))
    monkeypatch.setattr(settings_api, "ENV_PATH", env_file)
    monkeypatch.setattr(settings_api, "ENV_EXAMPLE_PATH", tmp_path / ".env.example")
    monkeypatch.setenv("CUA_ENABLED", "false")
    monkeypatch.setattr(main, "store", TraceStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(settings_desktop, "job", settings_desktop.InstallJob())
    monkeypatch.setattr(settings_desktop, "on_enabled", None)
    settings_desktop._invalidate_probe()
    config.get_settings.cache_clear()
    desktop.unregister_desktop_tools()
    yield env_file
    desktop.unregister_desktop_tools()
    settings_desktop._invalidate_probe()
    config.get_settings.cache_clear()


def _client(host="127.0.0.1"):
    from fastapi.testclient import TestClient

    from backend import main

    return TestClient(main.app, client=(host, 50000))


def _checks(monkeypatch, **overrides):
    from backend import settings_desktop

    state = {**ALL_OK, **overrides}
    monkeypatch.setattr(settings_desktop, "probe", lambda: dict(state))
    settings_desktop._invalidate_probe()
    return state


def _registered() -> set[str]:
    from backend.tools import registry

    return {t.name for t in registry.all_tools()} & DESKTOP_TOOLS


# --------------------------------------------------------------------------- status
def test_status_shape(env, monkeypatch):
    _checks(monkeypatch, image_present=False)
    data = _client().get("/settings/desktop", headers={"Origin": EXT}).json()
    assert data["enabled"] is False and data["ready"] is False
    assert data["packages_installed"] and data["docker_running"] and not data["image_present"]
    assert data["image_name"] == "ghcr.io/trycua/linux:24.04"
    assert data["installing"] is False and data["install_progress"] is None and data["last_error"] is None


def test_probe_detection(monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "packages_installed", lambda: True)
    monkeypatch.setattr(sd, "docker_path", lambda: None)
    assert sd.probe() == {"packages_installed": True, "docker_installed": False, "docker_running": False, "image_present": False}

    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 0 if cmd[1] == "info" else 1, "", "")

    monkeypatch.setattr(sd, "docker_path", lambda: "docker")
    monkeypatch.setattr(sd, "_run", fake_run)
    assert sd.probe() == {"packages_installed": True, "docker_installed": True, "docker_running": True, "image_present": False}
    assert calls == ["info", "image"]

    def daemon_down(cmd, timeout):
        return subprocess.CompletedProcess(cmd, 1, "", "error during connect")

    monkeypatch.setattr(sd, "_run", daemon_down)
    assert sd.probe()["docker_running"] is False and sd.probe()["image_present"] is False


def test_packages_installed_uses_find_spec(monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd.importlib.util, "find_spec", lambda name: None if name == "rapidocr" else object())
    assert sd.packages_installed() is False
    monkeypatch.setattr(sd.importlib.util, "find_spec", lambda name: object())
    assert sd.packages_installed() is True


# --------------------------------------------------------------------------- guard
def test_guard_rejects_remote_and_foreign_origin(env, monkeypatch):
    _checks(monkeypatch)
    assert _client(host="192.168.1.20").get("/settings/desktop").status_code == 403
    assert _client().get("/settings/desktop", headers={"Origin": "https://evil.example"}).status_code == 403
    assert _client().put("/settings/desktop", headers={"Origin": "https://evil.example"}, json={"enabled": True}).status_code == 403
    assert _client(host="10.0.0.5").post("/settings/desktop/install", json={}).status_code == 403
    assert "CUA_ENABLED=false" in env.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- enable / disable
def test_enable_disable_live(env, monkeypatch):
    from backend import config, settings_desktop
    from backend.agents.router import RouterAgent
    from backend.agents.router import RouterDecision
    from backend.tools import registry

    hook = []
    monkeypatch.setattr(settings_desktop, "on_enabled", lambda: hook.append(1))
    _checks(monkeypatch)
    c = _client()
    assert _registered() == set()
    r = c.put("/settings/desktop", headers={"Origin": EXT}, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert _registered() == DESKTOP_TOOLS
    assert {t.name for t in registry.list_for("desktop_task")} == DESKTOP_TOOLS
    assert config.get_settings().cua_enabled is True and hook == [1]
    assert c.get("/health").json()["cua_enabled"] is True
    text = env.read_bytes().decode("utf-8")
    assert "CUA_ENABLED=true\r\n" in text and "# --- Masaüstü ---" in text and "TIMEZONE=Europe/Istanbul" in text
    decision = RouterDecision(route="desktop_task", confidence=0.9, why="x")
    assert RouterAgent._downgrade_desktop(decision, tab=None, cua_enabled=config.get_settings().cua_enabled).route == "desktop_task"

    r = c.put("/settings/desktop", headers={"Origin": EXT}, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert _registered() == set() and registry.list_for("desktop_task") == []
    assert c.get("/health").json()["cua_enabled"] is False
    assert "CUA_ENABLED=false\r\n" in env.read_bytes().decode("utf-8")


def test_enable_refused_when_not_ready(env, monkeypatch):
    c = _client()
    _checks(monkeypatch, docker_running=False)
    r = c.put("/settings/desktop", json={"enabled": True})
    assert r.status_code == 409 and "Docker Desktop çalışmıyor" in r.json()["detail"]
    _checks(monkeypatch, docker_installed=False, docker_running=False, image_present=False)
    assert "kurulu değil" in c.put("/settings/desktop", json={"enabled": True}).json()["detail"]
    _checks(monkeypatch, image_present=False)
    r = c.put("/settings/desktop", json={"enabled": True})
    assert r.status_code == 409 and "imajı" in r.json()["detail"]
    assert _registered() == set() and "CUA_ENABLED=false" in env.read_text(encoding="utf-8")
    # disabling never needs readiness
    assert c.put("/settings/desktop", json={"enabled": False}).status_code == 200


def test_put_validates_body(env, monkeypatch):
    _checks(monkeypatch)
    c = _client()
    assert c.put("/settings/desktop", json={"enabled": "yes"}).status_code == 422
    assert c.put("/settings/desktop", json={}).status_code == 422
    assert c.put("/settings/desktop", content=b"[1]", headers={"Content-Type": "application/json"}).status_code == 422


def test_live_shots_router_mount():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import main

    app = FastAPI()
    c = TestClient(app)
    assert c.get("/shots/nope.png").status_code == 404
    saved = main._shots_mounted
    try:
        main._shots_mounted = False
        assert main.ensure_shots_router(app) is True
        r = c.get("/shots/nope.png")
        assert r.status_code == 404 and "Ekran görüntüsü" in r.json()["detail"]  # served by shots_router
    finally:
        main._shots_mounted = saved


# --------------------------------------------------------------------------- install job
class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.code = code

    def wait(self):
        return self.code

    def kill(self):
        pass


def _wait(job, timeout=5.0):
    deadline = time.monotonic() + timeout
    while job.snapshot()["installing"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not job.snapshot()["installing"]


def test_install_happy_path_with_enable(env, monkeypatch):
    from backend import settings_desktop as sd

    state = {"pkgs": False, "image": False}
    commands = []
    seen_progress = []

    def fake_popen(cmd, env=None):
        commands.append(cmd)
        if cmd[1:3] == ["pip", "install"] or cmd[1:4] == ["-m", "pip", "install"]:
            state["pkgs"] = True
            return FakeProc(["Collecting cua-sandbox==0.9.0", "Installed 12 packages"])
        assert cmd[1:] == ["pull", sd.IMAGE_NAME]
        state["image"] = True
        lines = ["24.04: Pulling from trycua/linux"]
        for layer in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
            lines.append(f"{layer}: Pulling fs layer")
        lines += ["aaaaaaaaaaaa: Download complete", "aaaaaaaaaaaa: Pull complete", "bbbbbbbbbbbb: Pull complete",
                  "Digest: sha256:abc", f"Status: Downloaded newer image for {sd.IMAGE_NAME}"]
        return FakeProc(lines)

    monkeypatch.setattr(sd, "_popen", fake_popen)
    monkeypatch.setattr(sd, "packages_installed", lambda: state["pkgs"])
    monkeypatch.setattr(sd, "docker_path", lambda: "docker")
    monkeypatch.setattr(sd, "docker_running", lambda d: True)
    monkeypatch.setattr(sd, "image_present", lambda d: state["image"])
    monkeypatch.setattr(sd, "find_uv", lambda: r"C:\x\tools\uv\uv.exe")
    orig = sd.InstallJob.set_progress

    def spy(self, step, message, percent=None):
        seen_progress.append((step, percent))
        orig(self, step, message, percent)

    monkeypatch.setattr(sd.InstallJob, "set_progress", spy)
    _checks(monkeypatch, packages_installed=False, image_present=False)

    r = _client().post("/settings/desktop/install", headers={"Origin": EXT}, json={"enable": True})
    assert r.status_code == 200 and r.json()["started"] is True
    _wait(sd.job)
    snap = sd.job.snapshot()
    assert snap["last_error"] is None and snap["install_progress"]["step"] == "done"
    assert commands[0][0].endswith("uv.exe") and commands[0][1:4] == ["pip", "install", "--python"]
    assert commands[1] == ["docker", "pull", sd.IMAGE_NAME]
    image_pcts = [p for s, p in seen_progress if s == "image" and p is not None]
    assert image_pcts and image_pcts[-1] == 100 and image_pcts == sorted(image_pcts)
    assert _registered() == DESKTOP_TOOLS and "CUA_ENABLED=true" in env.read_text(encoding="utf-8")


def test_install_is_idempotent_and_single(env, monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "_popen", lambda *a, **k: pytest.fail("nothing should be installed"))
    monkeypatch.setattr(sd, "packages_installed", lambda: True)
    monkeypatch.setattr(sd, "docker_path", lambda: "docker")
    monkeypatch.setattr(sd, "docker_running", lambda d: True)
    monkeypatch.setattr(sd, "image_present", lambda d: True)
    _checks(monkeypatch)
    job = sd.job
    with job.lock:
        job.installing = True  # pretend a job is running
    r = _client().post("/settings/desktop/install", json={})
    assert r.json()["started"] is False and r.json()["installing"] is True
    with job.lock:
        job.installing = False
    assert _client().post("/settings/desktop/install", json={}).json()["started"] is True
    _wait(job)
    assert job.snapshot()["last_error"] is None
    assert _registered() == set()  # enable not requested


@pytest.mark.parametrize(
    "docker,running,expect",
    [(None, False, "kurulu değil"), ("docker", False, "Docker Desktop çalışmıyor, açıp tekrar dene")],
)
def test_install_docker_errors(env, monkeypatch, docker, running, expect):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "packages_installed", lambda: True)
    monkeypatch.setattr(sd, "docker_path", lambda: docker)
    monkeypatch.setattr(sd, "docker_running", lambda d: running)
    _checks(monkeypatch, docker_installed=bool(docker), docker_running=False, image_present=False)
    _client().post("/settings/desktop/install", json={"enable": True})
    _wait(sd.job)
    snap = sd.job.snapshot()
    assert expect in snap["last_error"] and snap["install_progress"] is None
    assert _registered() == set() and "CUA_ENABLED=false" in env.read_text(encoding="utf-8")


def test_install_package_failure_message(env, monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "packages_installed", lambda: False)
    monkeypatch.setattr(sd, "find_uv", lambda: None)
    monkeypatch.setattr(sd, "_popen", lambda cmd, env=None: FakeProc(["ERROR: Could not connect: timed out"], code=1))
    _checks(monkeypatch, packages_installed=False)
    _client().post("/settings/desktop/install", json={})
    _wait(sd.job)
    err = sd.job.snapshot()["last_error"]
    assert "Python paketleri kurulamadı" in err and "İnternet" in err


def test_pip_fallback_command(monkeypatch):
    import sys

    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "find_uv", lambda: None)
    cmd, _ = sd.install_command()
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"] and cmd[-1].endswith("requirements-cua.txt")


def test_pull_failure_daemon(env, monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "packages_installed", lambda: True)
    monkeypatch.setattr(sd, "docker_path", lambda: "docker")
    monkeypatch.setattr(sd, "docker_running", lambda d: True)
    monkeypatch.setattr(sd, "image_present", lambda d: False)
    monkeypatch.setattr(
        sd, "_popen", lambda cmd, env=None: FakeProc(["error during connect: open //./pipe/docker_engine"], code=1)
    )
    _checks(monkeypatch, image_present=False)
    _client().post("/settings/desktop/install", json={})
    _wait(sd.job)
    assert "Docker Desktop çalışmıyor" in sd.job.snapshot()["last_error"]


def test_pull_progress_parser():
    from backend.settings_desktop import PullProgress

    p = PullProgress()
    assert p.feed("24.04: Pulling from trycua/linux") is None
    assert p.feed("aaaaaaaaaaaa: Pulling fs layer") == 0
    assert p.feed("bbbbbbbbbbbb: Already exists") == 50
    assert p.feed("aaaaaaaaaaaa: Download complete") == 80
    assert p.feed("aaaaaaaaaaaa: Pull complete") == 100


def test_uv_env_uses_copy_link_mode(monkeypatch):
    from backend import settings_desktop as sd

    monkeypatch.setattr(sd, "find_uv", lambda: r"C:\x\tools\uv\uv.exe")
    cmd, env = sd.install_command()
    assert env["UV_LINK_MODE"] == "copy" and cmd[1:3] == ["pip", "install"]


def test_install_retries_on_file_lock(env, monkeypatch):
    from backend import settings_desktop as sd

    state = {"pkgs": False, "calls": 0}

    def fake_popen(cmd, env=None):
        state["calls"] += 1
        if state["calls"] < 3:
            return FakeProc([r"error: failed to remove directory `x\site-packages\a.data`: (os error 32)"], code=2)
        state["pkgs"] = True
        return FakeProc(["Installed 3 packages"])

    monkeypatch.setattr(sd, "PIP_RETRY_DELAY_S", 0)
    monkeypatch.setattr(sd, "_popen", fake_popen)
    monkeypatch.setattr(sd, "packages_installed", lambda: state["pkgs"])
    monkeypatch.setattr(sd, "docker_path", lambda: "docker")
    monkeypatch.setattr(sd, "docker_running", lambda d: True)
    monkeypatch.setattr(sd, "image_present", lambda d: True)
    _checks(monkeypatch, packages_installed=False)
    _client().post("/settings/desktop/install", json={})
    _wait(sd.job)
    assert state["calls"] == 3 and sd.job.snapshot()["last_error"] is None


def test_install_lock_error_gives_up_after_three(env, monkeypatch):
    from backend import settings_desktop as sd

    calls = []
    monkeypatch.setattr(sd, "PIP_RETRY_DELAY_S", 0)
    monkeypatch.setattr(
        sd, "_popen",
        lambda cmd, env=None: calls.append(1) or FakeProc(["The process cannot access the file because it is being used by another process"], code=1),
    )
    monkeypatch.setattr(sd, "packages_installed", lambda: False)
    _checks(monkeypatch, packages_installed=False)
    _client().post("/settings/desktop/install", json={})
    _wait(sd.job)
    assert len(calls) == 3 and "kilitli" in sd.job.snapshot()["last_error"]
