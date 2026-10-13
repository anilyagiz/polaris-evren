"""Start the real Polaris backend for E2E runs (subprocess entry point).

Differences from `python -m backend.main`: only that the hard-coded Brave
endpoint is redirected to the fake Brave server (E2E_BRAVE_URL) so `web.search`
runs its real code path against a local mock.  Everything else is configured
through environment variables set by scripts/e2e/harness.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402

brave = os.environ.get("E2E_BRAVE_URL")
if brave:
    try:
        import backend.tools.search as search  # noqa: E402

        search.BRAVE_URL = brave
    except Exception as exc:  # the search tool is allowed to be broken; the scenario will say so
        print(f"[e2e-launcher] could not patch BRAVE_URL: {exc!r}", flush=True)

from backend.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["E2E_PORT"]), log_level="info")
