"""Starting child processes without opening console windows on Windows.

When the parent has no console (for example ``tableforge ui`` started from a shortcut, or
any process started with ``DETACHED_PROCESS``), Windows opens a new console window for
every console program it starts. ``CREATE_NO_WINDOW`` gives the child a hidden console
instead. That console is inherited by grandchildren, which matters for virtual
environments: ``.venv\\Scripts\\python.exe`` is a launcher that starts the real Python.

``DETACHED_PROCESS`` must not be combined with ``CREATE_NO_WINDOW``: Windows then ignores
``CREATE_NO_WINDOW``, and the launcher's child opens a visible window.
"""

from __future__ import annotations

import subprocess
import sys

STOP_FILE_ENV = "TABLEFORGE_STOP_FILE"
"""A child that sees this file appear stops as if it received Ctrl+C.

A process with a hidden console cannot be sent Ctrl+Break by a parent that has a different
console, so ``tableforge ui`` asks its workers to stop by creating this file instead.
"""


def no_window_flags() -> int:
    """Flags for short helper commands (taskkill, nvidia-smi, tailscale): no window."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def background_process_flags() -> int:
    """Flags for long-running children: hidden console and their own Ctrl+Break group."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0
