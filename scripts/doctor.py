"""Polaris doctor: ortami kontrol eder ve Turkce bir kontrol listesi yazar.

Kullanim:  python scripts/doctor.py   (venv gerekmez; yalnizca standart kutuphane)
Gizli degerleri ASLA yazdirmaz; yalnizca "dolu/bos" bilgisi verir.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST, PORT = "127.0.0.1", 8000

OK, WARN, FAIL = "[ OK ]", "[UYARI]", "[HATA]"
results: list[tuple[str, str, str]] = []


def add(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, shell=(sys.platform == "win32")
        )
    except Exception:
        return None
    return (out.stdout or out.stderr).strip() if out.returncode == 0 else None


def check_python() -> None:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (3, 11) <= (v.major, v.minor) < (3, 14):
        add(OK, "Python", ver)
    elif (v.major, v.minor) >= (3, 14):
        add(WARN, "Python", f"{ver} - 3.14 faster-whisper icin riskli; 3.12 onerilir")
    else:
        add(FAIL, "Python", f"{ver} - 3.11+ gerekli")


def check_node() -> None:
    out = run(["node", "--version"])
    if not out:
        add(FAIL, "Node.js", "bulunamadi (https://nodejs.org, 18+)")
    else:
        try:
            major = int(out.lstrip("v").split(".")[0])
        except ValueError:
            major = 0
        add(OK if major >= 18 else FAIL, "Node.js", out + ("" if major >= 18 else " - 18+ gerekli"))
    pn = run(["pnpm", "--version"])
    add(OK if pn else FAIL, "pnpm", pn or "bulunamadi (npm install -g pnpm)")


def check_venv() -> None:
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    py = ROOT / ".venv" / sub
    if not py.exists():
        add(FAIL, ".venv", "yok -> .\\scripts\\setup.ps1")
        return
    add(OK, ".venv", str(py.relative_to(ROOT)))
    missing = []
    for mod in ("fastapi", "uvicorn", "openai", "httpx", "faster_whisper", "edge_tts"):
        r = subprocess.run([str(py), "-c", f"import {mod}"], capture_output=True)
        if r.returncode != 0:
            missing.append(mod)
    if missing:
        add(FAIL, "Backend paketleri", "eksik: " + ", ".join(missing) + " -> setup.ps1")
    else:
        add(OK, "Backend paketleri", "kurulu")


def read_env() -> dict[str, str]:
    path = ROOT / "backend" / ".env"
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.split(" #")[0].strip().strip('"').strip("'")
        vals[k.strip()] = v
    return vals


PLACEHOLDERS = {"", "buraya_anahtarini_yaz"}


def check_env() -> dict[str, str]:
    path = ROOT / "backend" / ".env"
    if not path.exists():
        add(FAIL, "backend/.env", "yok -> setup.ps1 (veya .env.example'i kopyalayin)")
        return {}
    env = read_env()
    add(OK, "backend/.env", "mevcut")
    for key, required in (("DEEPSEEK_API_KEY", True), ("LLM_MODEL", False), ("BRAVE_API_KEY", False)):
        if env.get(key, "") not in PLACEHOLDERS:
            add(OK, f"  {key}", "dolu (deger gizli)")
        elif required:
            add(FAIL, f"  {key}", "bos - LLM calismaz")
        elif key == "LLM_MODEL":
            add(WARN, f"  {key}", "bos - varsayilan (deepseek-chat) kullanilir")
        else:
            add(WARN, f"  {key}", "bos - web.search hata doner")
    return env


def port_in_use() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def check_backend() -> None:
    if not port_in_use():
        add(WARN, f"Port {PORT}", "bos - backend calismiyor (.\\scripts\\run-backend.ps1)")
        return
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=3) as r:
            data = json.loads(r.read().decode())
        if data.get("ok"):
            add(OK, f"Port {PORT} /health", "backend yanit veriyor")
            if data.get("llm_configured") is False:
                add(WARN, "  llm_configured", "false - anahtar backend'e ulasmamis; .env sonrasi yeniden baslatin")
            add(OK, "  cua_enabled", str(data.get("cua_enabled")))
        else:
            add(FAIL, "GET /health", f"ok degil: {data}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        add(FAIL, f"Port {PORT}", f"dolu ama /health yanit vermiyor ({type(exc).__name__}); baska program olabilir")


def check_extension() -> None:
    dist = ROOT / "extension" / "dist"
    if (dist / "manifest.json").exists():
        add(OK, "extension/dist", "derlenmis")
    else:
        add(FAIL, "extension/dist", "yok -> .\\scripts\\build-extension.ps1")
    nm = (ROOT / "extension" / "node_modules").exists()
    add(OK if nm else WARN, "extension/node_modules", "kurulu" if nm else "yok -> pnpm install")


def check_docker(env: dict[str, str]) -> None:
    want = env.get("CUA_ENABLED", "false").lower() in ("1", "true", "yes")
    bad = FAIL if want else WARN
    if not shutil.which("docker"):
        add(bad, "Docker (CUA)", "bulunamadi - yalniz masaustu demosu icin gerekir (docs/cua-setup.md)")
        return
    ver = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if ver:
        add(OK, "Docker (CUA)", f"calisiyor (surum {ver})")
    else:
        add(bad, "Docker (CUA)", "kurulu ama motor kapali - Docker Desktop'i baslatin")
    add(OK, "CUA_ENABLED", "true" if want else "false (masaustu kapali; tarayici demosu etkilenmez)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    check_python()
    check_node()
    check_venv()
    env = check_env()
    check_backend()
    check_extension()
    check_docker(env)
    add(OK, "ffmpeg", "gerekmez (PyAV webm/opus'u cozer)")

    print("\nPolaris Doctor\n" + "=" * 64)
    for status, name, detail in results:
        print(f"{status} {name:<26} {detail}")
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    print("=" * 64)
    if fails:
        print(f"{fails} hata, {warns} uyari. Hatalari yukaridaki onerilerle giderin.")
    elif warns:
        print(f"Hata yok, {warns} uyari. Temel demo calisabilir.")
    else:
        print("Her sey hazir.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
