"""`python -m tray` — start the Polaris tray app (or `--smoke`)."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
