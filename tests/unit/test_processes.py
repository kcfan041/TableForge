from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tableforge.processes import STOP_FILE_ENV, background_process_flags, no_window_flags


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process flags")
def test_children_get_a_hidden_console_and_are_never_detached() -> None:
    # DETACHED_PROCESS would make Windows ignore CREATE_NO_WINDOW and let the
    # .venv python launcher's child open a visible console window.
    for flags in (no_window_flags(), background_process_flags()):
        assert flags & subprocess.CREATE_NO_WINDOW
        assert not flags & subprocess.DETACHED_PROCESS
    assert background_process_flags() & subprocess.CREATE_NEW_PROCESS_GROUP


def test_stop_file_sets_the_stop_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal

    from tableforge.cli.main import _stop_event

    stop_file = tmp_path / "stop"
    monkeypatch.setenv(STOP_FILE_ENV, str(stop_file))
    handlers = {
        name: signal.getsignal(getattr(signal, name))
        for name in ("SIGINT", "SIGTERM", "SIGBREAK")
        if hasattr(signal, name)
    }
    try:
        stop = _stop_event()
        assert not stop.wait(0.5)
        stop_file.write_text("now", encoding="utf-8")
        assert stop.wait(5)
    finally:
        for name, handler in handlers.items():
            signal.signal(getattr(signal, name), handler)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows")
def test_no_flags_outside_windows() -> None:
    assert no_window_flags() == 0
    assert background_process_flags() == 0
