"""Polaris Windows system-tray launcher.  Owner: P6.

Runs the backend as a hidden child process, shows its health as a tray icon and
offers a small Turkish menu.  It never imports ``backend`` itself, so it can be
frozen into a tiny ``Polaris.exe`` that uses the install's ``.venv``.
"""

APP_NAME = "Polaris"
__version__ = "0.1.0"
