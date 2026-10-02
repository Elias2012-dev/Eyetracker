"""Where files live - source checkout vs. a frozen (PyInstaller) build.

Two roots matter, and mixing them up is the classic way a packaged app
breaks:

* :func:`bundle_dir` holds **read-only resources shipped with the app**:
  the bridge DLLs and (optionally) the MediaPipe model. In a one-file
  build this is PyInstaller's extraction directory, which exists for as
  long as the process runs - long enough for ETS2/ATS to load
  ``NPClient64.dll`` out of it.
* :func:`data_dir` holds **everything the app writes**: config,
  calibration, the registry backup, the log. From source that is the
  repository root; in a frozen build it is ``%APPDATA%\\Eyetracker``,
  because writing next to the exe fails under Program Files and writing
  into the extraction directory would throw the files away on exit.

Run ``python -m eyetrack --paths`` to see both.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller sets sys.frozen; a plain interpreter never has it.
FROZEN = bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only resources: the repo root when running from source."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable per-user directory (created when possible)."""
    if FROZEN:
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "Eyetracker"
    else:
        path = Path(__file__).resolve().parent.parent
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:  # read-only medium: callers surface the real error
        pass
    return path


def bridge_dir() -> Path:
    """Folder holding NPClient.dll / NPClient64.dll."""
    return bundle_dir() / "bridge"


def config_path() -> Path:
    return data_dir() / "eyetrack.json"


def calibration_path() -> Path:
    return data_dir() / "calibration.json"


def backup_path() -> Path:
    return data_dir() / "bridge_registry_backup.json"


def log_path() -> Path:
    return data_dir() / "Eyetracker.log"


def model_path() -> Path:
    """Where the MediaPipe face landmarker lives.

    A bundled copy is preferred when it exists (so the exe works offline
    on first run); otherwise the writable per-user directory, where
    :func:`eyetrack.pose.ensure_model` can download it.
    """
    bundled = bundle_dir() / "models" / "face_landmarker.task"
    if bundled.exists():
        return bundled
    return data_dir() / "models" / "face_landmarker.task"


def describe() -> str:
    """Human-readable summary for ``--paths``."""
    return "\n".join([
        f"frozen build : {FROZEN}",
        f"bundle (ro)  : {bundle_dir()}",
        f"data   (rw)  : {data_dir()}",
        f"config       : {config_path()}",
        f"calibration  : {calibration_path()}",
        f"registry bak : {backup_path()}",
        f"log          : {log_path()}",
        f"bridge dlls  : {bridge_dir()}",
        f"model        : {model_path()}"
        f"{'' if model_path().exists() else '  (missing - will download)'}",
        f"executable   : {sys.executable}",
    ])