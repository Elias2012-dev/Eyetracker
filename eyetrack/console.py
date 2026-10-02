"""Console handling for a windowed build.

The packaged exe has no terminal of its own, and when someone double-clicks
it the process may have a stdout handle that silently swallows everything.
Two small pieces make the diagnostics survive:

* :func:`attach_log` **tees** stdout/stderr into ``Eyetracker.log``, so every
  line the tracker prints is captured whether it was launched from Explorer,
  from a script, or from a terminal. From source that costs one log file
  next to the config (already covered by ``*.log`` in .gitignore).
* :func:`show_message` pops up a dialog for the short interactive commands
  (list cameras, list games, install bridge) - the places where the printed
  output *is* the result. Set ``EYE_TRACKER_NO_DIALOG=1`` for unattended
  runs.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

from .paths import log_path


class _NullStream(io.TextIOBase):
    """Stands in for a missing stdout so writes go to the log only."""

    def write(self, data: str) -> int:  # noqa: D102
        return len(data)

    def flush(self) -> None:  # noqa: D102
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno")


class _Tee(io.TextIOBase):
    """Write to the original stream *and* to the log file."""

    def __init__(self, original, path: Path) -> None:
        self._original = original if original is not None else _NullStream()
        self._file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, data: str) -> int:
        for stream in (self._original, self._file):
            try:
                stream.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for stream in (self._original, self._file):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._original.fileno()

    @property
    def encoding(self) -> str:  # some libraries probe this
        return getattr(self._original, "encoding", "utf-8") or "utf-8"


def has_console() -> bool:
    """True when there is a real stdout to print to."""
    return sys.stdout is not None


def attach_log() -> Path | None:
    """Tee stdout/stderr into the log file. Returns the path, or None."""
    path = log_path()
    try:
        tee = _Tee(sys.stdout, path)
    except OSError:
        return None
    sys.stdout = tee  # type: ignore[assignment]
    sys.stderr = tee  # type: ignore[assignment]
    print(f"\n=== Eyetracker started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    try:
        tee.flush()
    except Exception:
        pass
    return path


def show_message(title: str, text: str) -> None:
    """Show ``text`` in a dialog - only when there is no console.

    Set ``EYE_TRACKER_NO_DIALOG=1`` to suppress dialogs for scripted or
    unattended runs; the text still goes to the log.
    """
    if has_console() or sys.platform != "win32" or not text:
        return
    if os.environ.get("EYE_TRACKER_NO_DIALOG") == "1":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                       wintypes.LPCWSTR, wintypes.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, text[:2000], title, 0x00000040)  # MB_ICONINFORMATION
    except Exception:  # never let a dialog failure break the command
        pass