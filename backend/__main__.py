"""`python -m backend` — start the Polaris backend with a Turkish banner.

Owner: K3 (P3).  Uses HOST/PORT from backend/.env (defaults 127.0.0.1:8765);
`--host` / `--port` override them.  Secret values are never printed.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import APP_VERSION, config_warnings, get_settings, missing_keys
from .logging_setup import LOG_FILE, ensure_utf8_console, setup_logging


def banner(host: str, port: int) -> str:
    """Startup banner text (pure; tested)."""

    settings = get_settings()
    shown_host = "localhost" if host in {"127.0.0.1", "::1", "localhost"} else host
    missing = missing_keys(settings)
    lines = [
        "=" * 60,
        f"  Polaris backend {APP_VERSION}",
        f"  Adres      : http://{shown_host}:{port}  (sağlık: /health)",
        f"  Model      : {settings.llm_model}",
        f"  Masaüstü   : {'açık' if settings.cua_enabled else 'kapalı'} (CUA_ENABLED)",
        f"  Log dosyası: {LOG_FILE}",
        f"  Eksik ayar : {', '.join(missing) if missing else 'yok'}",
    ]
    for warning in config_warnings(settings):
        lines.append(f"  UYARI: {warning}")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        lines.append("  UYARI: HOST yerel değil; asistan ağdaki diğer cihazlara açık olabilir.")
    lines.append("  Durdurmak için Ctrl+C.")
    lines.append("=" * 60)
    return "\n".join(lines)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m backend", description="Polaris backend sunucusu")
    parser.add_argument("--host", default=settings.host, help=f"dinlenecek adres (varsayılan {settings.host})")
    parser.add_argument("--port", type=int, default=settings.port, help=f"port (varsayılan {settings.port})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()
    args = _parse(argv)
    setup_logging()
    print(banner(args.host, args.port), flush=True)

    import uvicorn

    from .main import app

    try:
        # log_config=None: uvicorn's loggers propagate to our console + file handlers.
        uvicorn.run(app, host=args.host, port=args.port, log_config=None, reload=False)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    except OSError as exc:  # port in use etc.
        logging.getLogger("backend").error("Sunucu başlatılamadı (%s:%s): %s", args.host, args.port, exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
