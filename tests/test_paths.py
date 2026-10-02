"""Path resolution has to differ between a source checkout and a frozen exe.

The failure this guards against is subtle: in a one-file build, writing the
config or the calibration into PyInstaller's extraction directory means the
settings silently vanish when the process exits, while pointing the bridge
at a copied DLL means the games never load it.
"""

import sys
from pathlib import Path

import pytest

from eyetrack import console, paths

REPO_ROOT = Path(paths.__file__).resolve().parent.parent


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Simulate a PyInstaller one-file build."""
    meipass = tmp_path / "_MEI12345"
    meipass.mkdir()
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    return meipass


def test_source_mode_keeps_everything_in_the_repo_root(monkeypatch):
    monkeypatch.setattr(paths, "FROZEN", False)
    assert paths.bundle_dir() == REPO_ROOT
    assert paths.data_dir() == REPO_ROOT
    assert paths.bridge_dir() == REPO_ROOT / "bridge"


def test_frozen_splits_read_only_bundle_from_writable_data_dir(frozen, tmp_path):
    assert paths.bundle_dir() == frozen
    data = paths.data_dir()
    assert data == tmp_path / "AppData" / "Roaming" / "Eyetracker"
    assert data.is_dir(), "the writable directory must be created on demand"
    for path in (paths.config_path(), paths.calibration_path(),
                 paths.backup_path(), paths.log_path()):
        assert path.parent == data, f"{path} must not be written into the bundle"


def test_frozen_ships_the_bridge_dlls_in_the_bundle(frozen):
    assert paths.bridge_dir() == frozen / "bridge"


def test_bundled_model_wins_so_the_exe_works_offline(frozen):
    (frozen / "models").mkdir()
    (frozen / "models" / "face_landmarker.task").write_bytes(b"bundled")
    assert paths.model_path() == frozen / "models" / "face_landmarker.task"


def test_missing_model_falls_back_to_the_writable_dir(frozen, tmp_path):
    # No bundled copy: the download has to land somewhere it can survive.
    assert paths.model_path() == (
        tmp_path / "AppData" / "Roaming" / "Eyetracker" / "models" / "face_landmarker.task"
    )


def test_describe_lists_both_roots_and_is_printable():
    text = paths.describe()
    assert "bundle (ro)" in text and "data   (rw)" in text
    assert paths.bridge_dir().name == "bridge"


def test_show_message_is_a_noop_when_a_console_exists(monkeypatch):
    monkeypatch.setattr(console, "has_console", lambda: True)
    console.show_message("title", "text")  # must not raise


def test_no_dialog_env_suppresses_the_popup(monkeypatch):
    """Scripted/CI runs must never block on a modal dialog."""
    import ctypes

    monkeypatch.setattr(console, "has_console", lambda: False)
    monkeypatch.setenv("EYE_TRACKER_NO_DIALOG", "1")
    calls = []
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: calls.append(1))
    console.show_message("title", "text")
    assert not calls, "the popup must not be created"


def test_data_dir_survives_an_unwritable_target(monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setenv("APPDATA", str(blocker / "nested"))
    # No exception: the real failure surfaces when a file is written, with a
    # message naming the path.
    assert paths.data_dir() == blocker / "nested" / "Eyetracker"