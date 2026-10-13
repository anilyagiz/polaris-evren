"""Console-less entry point: `pythonw tray\\launch.pyw` (autostart, shortcuts, PyInstaller).

Works from any current directory: puts the repo/install root on sys.path first.
"""

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tray.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
