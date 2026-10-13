import sqlite3
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from backend import config, db


def test_defaults(settings_env):
    s = settings_env()
    assert s.llm_model == "deepseek-chat" and s.timezone == "Europe/Istanbul"
    assert s.port == 8765 and s.tool_timeout_s == 20.0 and s.confirm_timeout_s == 60.0
    assert s.max_tool_calls == 12 and s.request_budget_s == 240.0 and s.max_revisions == 2
    assert s.cua_enabled is False and s.llm_configured is False
    assert s.desktop_parser == "ocr"


def test_env_parsing(settings_env):
    s = settings_env(DEEPSEEK_API_KEY="  sk-x ", PORT="9001", TOOL_TIMEOUT_S="2.5", CUA_ENABLED="Yes",
                     MAX_TOOL_CALLS="3", DESKTOP_PARSER="OCR")
    assert s.deepseek_api_key == "sk-x" and s.llm_configured
    assert s.port == 9001 and s.tool_timeout_s == 2.5 and s.max_tool_calls == 3
    assert s.cua_enabled is True and s.desktop_parser == "ocr"


@pytest.mark.parametrize("raw,expected", [("1", True), ("on", True), ("TRUE", True), ("0", False), ("nah", False), ("", False)])
def test_bool_parsing(settings_env, raw, expected):
    assert settings_env(CUA_ENABLED=raw).cua_enabled is expected


def test_invalid_numbers_fall_back(settings_env):
    s = settings_env(PORT="abc", TOOL_TIMEOUT_S="x")
    assert s.port == 8765 and s.tool_timeout_s == 20.0


def test_blank_env_uses_default(settings_env):
    assert settings_env(LLM_MODEL="   ").llm_model == "deepseek-chat"


def test_timezone_and_now(settings_env):
    s = settings_env(TIMEZONE="Europe/Istanbul")
    assert config.now_local().utcoffset() == timedelta(hours=3)
    assert config.now_iso().endswith("+03:00")
    settings_env(TIMEZONE="UTC")
    assert config.now_iso().endswith("+00:00")


def test_bad_timezone_falls_back_to_utc(settings_env):
    s = settings_env(TIMEZONE="Not/AZone")
    assert s.tz == ZoneInfo("UTC")


def test_ensure_data_dir_creates_parent(settings_env, tmp_path):
    target = tmp_path / "deep" / "x.db"
    settings_env(DB_PATH=str(target))
    assert config.ensure_data_dir() == target and target.parent.is_dir()


def test_settings_frozen(settings_env):
    with pytest.raises(Exception):
        settings_env().port = 1


# ---- db.py -----------------------------------------------------------------
@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    path = tmp_path / "sub" / "a.db"
    monkeypatch.setenv("ASSISTANT_DB", str(path))
    monkeypatch.setattr(db, "_ENV_LOADED", True)
    return path


def test_db_path_override_and_conn(isolated_db):
    assert db.db_path() == isolated_db.resolve()
    conn = db.get_conn()
    try:
        assert isolated_db.parent.is_dir()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_session_commits_and_rolls_back(isolated_db):
    with db.session() as c:
        c.execute("CREATE TABLE t (v INTEGER)")
        c.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError):
        with db.session() as c:
            c.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("boom")
    with db.session() as c:
        assert [r["v"] for r in c.execute("SELECT v FROM t")] == [1]


def test_load_env_parser(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('A_T1=plain   # c\n# comment\nB_T1="q # not comment"\nC_T1=\'s\'\nD_T1\nE_T1=keep\n', encoding="utf-8")
    monkeypatch.setattr(db, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(db, "_ENV_LOADED", False)
    monkeypatch.setenv("E_T1", "real")
    for k in ("A_T1", "B_T1", "C_T1", "D_T1"):
        monkeypatch.delenv(k, raising=False)
    db.load_env()
    import os
    try:
        assert os.environ["A_T1"] == "plain" and os.environ["B_T1"] == "q # not comment"
        assert os.environ["C_T1"] == "s" and "D_T1" not in os.environ
        assert os.environ["E_T1"] == "real"  # real env wins
    finally:
        for k in ("A_T1", "B_T1", "C_T1"):
            os.environ.pop(k, None)


def test_register_once(isolated_db):
    from backend.tools import registry as reg

    async def h(a, c):
        return {"ok": True}

    saved = dict(reg._REGISTRY)
    reg.clear()
    try:
        t = reg.Tool(name="x.y", description="d", parameters={"type": "object"}, risk="safe", handler=h)
        db.register_once(t)
        db.register_once(t)
        assert reg.get("x.y") is t and len(reg.all_tools()) == 1
    finally:
        reg.clear()
        reg._REGISTRY.update(saved)
