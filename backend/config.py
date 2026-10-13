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

#: Product version reported by /health and the startup banner.
APP_VERSION = "0.2.0"

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
    #: False when LLM_MODEL is not set in the environment (default model used).
    llm_model_explicit: bool = True
    #: Max /stt + /tts requests per client per minute (each path counted separately).
    voice_rate_limit_per_min: int = 30
    #: TTS engine for POST /tts: "ema" (offline EMA Lightning, default) or "edge".
    tts_engine: str = "ema"
    #: EMA speaking speed, 0.25-4.
    tts_speed: float = 1.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def brave_configured(self) -> bool:
        return bool(self.brave_api_key)

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
        deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://evren-llmapi.ssyz.org.tr/v1"),
        # DeepSeek's OpenAI-compatible chat model id (docs: deepseek-chat /
        # deepseek-reasoner). The task is function-calling heavy, so the
        # non-reasoning chat model is the default.
        llm_model=_env("LLM_MODEL", "gemma-4-31b"),
        brave_api_key=_env("BRAVE_API_KEY"),
        stt_model=_env("STT_MODEL", "small"),
        tts_voice=_env("TTS_VOICE", "tr-TR-EmelNeural"),
        timezone=_env("TIMEZONE", "Europe/Istanbul"),
        cua_enabled=_env_bool("CUA_ENABLED", False),
        desktop_parser=_env("DESKTOP_PARSER", "ocr").lower(),
        db_path=db_path,
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8765),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 60.0),
        llm_temperature_chat=_env_float("LLM_TEMPERATURE_CHAT", 0.6),
        tool_timeout_s=_env_float("TOOL_TIMEOUT_S", 20.0),
        confirm_timeout_s=_env_float("CONFIRM_TIMEOUT_S", 60.0),
        max_tool_calls=_env_int("MAX_TOOL_CALLS", 40),
        request_budget_s=_env_float("REQUEST_BUDGET_S", 900.0),
        max_revisions=_env_int("MAX_REVISIONS", 2),
        reminder_poll_s=_env_float("REMINDER_POLL_S", 30.0),
        llm_model_explicit=bool(_env("LLM_MODEL")),
        voice_rate_limit_per_min=_env_int("VOICE_RATE_LIMIT_PER_MIN", 30),
        tts_engine=_env("TTS_ENGINE", "ema").lower(),
        tts_speed=_env_float("TTS_SPEED", 1.0),
    )


def reload_settings() -> Settings:
    """Drop the cached snapshot and re-read ``os.environ`` (live settings change)."""

    get_settings.cache_clear()
    return get_settings()


def config_warnings(settings: Settings | None = None) -> list[str]:
    """Turkish, human-readable problems with the configuration.

    Never includes secret values — only whether a key is present.
    """

    s = settings or get_settings()
    warnings: list[str] = []
    if not s.llm_configured:
        warnings.append(
            "DEEPSEEK_API_KEY tanımlı değil: LLM gerektiren istekler hata dönecek "
            "(backend/.env dosyasına ekleyin)."
        )
    if not s.llm_model_explicit:
        warnings.append(f"LLM_MODEL tanımlı değil: varsayılan model '{s.llm_model}' kullanılıyor.")
    if not s.brave_configured:
        warnings.append("BRAVE_API_KEY tanımlı değil: web araması anahtarsız DuckDuckGo yedeğiyle yapılacak.")
    return warnings


def missing_keys(settings: Settings | None = None) -> list[str]:
    """Names (only names) of unset environment keys that matter to users."""

    s = settings or get_settings()
    names: list[str] = []
    if not s.llm_configured:
        names.append("DEEPSEEK_API_KEY")
    if not s.llm_model_explicit:
        names.append("LLM_MODEL")
    if not s.brave_configured:
        names.append("BRAVE_API_KEY")
    return names


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
