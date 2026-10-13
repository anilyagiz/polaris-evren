"""Runtime settings loaded from backend/.env. Owner: K3.

Everything else in the backend imports settings from here so that no module
reads os.environ directly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "assistant.db"
PROMPTS_DIR = BACKEND_DIR / "prompts"

load_dotenv(BACKEND_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logger.warning("Invalid float for %s, using %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        logger.warning("Invalid int for %s, using %s", name, default)
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment."""

    deepseek_api_key: str
    deepseek_base_url: str
    llm_model: str
    brave_api_key: str
    stt_model: str
    tts_voice: str
    timezone: str
    cua_enabled: bool
    desktop_parser: str

    db_path: Path
    host: str
    port: int
    llm_timeout_s: float
    llm_temperature_chat: float
    tool_timeout_s: float
    confirm_timeout_s: float
    max_tool_calls: int
    request_budget_s: float
    max_revisions: int
    reminder_poll_s: float

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:  # pragma: no cover - bad TZ string
            logger.warning("Unknown TIMEZONE %r, falling back to UTC", self.timezone)
            return ZoneInfo("UTC")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    db_path = Path(_env("DB_PATH", str(DEFAULT_DB_PATH)))
    return Settings(
        deepseek_api_key=_env("DEEPSEEK_API_KEY"),
        deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        # DeepSeek's OpenAI-compatible chat model id (docs: deepseek-chat /
        # deepseek-reasoner). The task is function-calling heavy, so the
        # non-reasoning chat model is the default.
        llm_model=_env("LLM_MODEL", "deepseek-chat"),
        brave_api_key=_env("BRAVE_API_KEY"),
        stt_model=_env("STT_MODEL", "small"),
        tts_voice=_env("TTS_VOICE", "tr-TR-EmelNeural"),
        timezone=_env("TIMEZONE", "Europe/Istanbul"),
        cua_enabled=_env_bool("CUA_ENABLED", False),
        desktop_parser=_env("DESKTOP_PARSER", "ocr").lower(),
        db_path=db_path,
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 30.0),
        llm_temperature_chat=_env_float("LLM_TEMPERATURE_CHAT", 0.6),
        tool_timeout_s=_env_float("TOOL_TIMEOUT_S", 20.0),
        confirm_timeout_s=_env_float("CONFIRM_TIMEOUT_S", 60.0),
        max_tool_calls=_env_int("MAX_TOOL_CALLS", 8),
        request_budget_s=_env_float("REQUEST_BUDGET_S", 90.0),
        max_revisions=_env_int("MAX_REVISIONS", 2),
        reminder_poll_s=_env_float("REMINDER_POLL_S", 30.0),
    )


def now_local() -> datetime:
    """Current time in the configured timezone."""

    return datetime.now(get_settings().tz)


def now_iso() -> str:
    """ISO-8601 timestamp with offset, as required by TraceStep.ts."""

    return now_local().isoformat(timespec="seconds")


def ensure_data_dir() -> Path:
    """Create backend/data lazily (SQLite lives there)."""

    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
